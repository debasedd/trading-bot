"""
analysis/direction_ensemble.py — Agregator verdict multi-agen menjadi satu
probabilitas arah LONG/SHORT.

Fungsi di modul ini murni (tanpa I/O) supaya mudah diuji secara terpisah.

Mengapa log-odds pooling, bukan rata-rata probabilitas?
------------------------------------------------------
Rata-rata aritmetika dari beberapa probabilitas yang sudah terkalibrasi
justru menghasilkan hasil yang TIDAK terkalibrasi dan kehilangan ketajaman
(sharpness). Log-odds pooling menggabungkan setiap z-score secara shrunk
terhadap over-confidence, lalu sigmoid sekali di akhir. Scaling z-score
dengan faktor < 1 menarik probabilitas kembali ke 0.5 — itu bentuk paling
murah dari kalibrasi yang tidak butuh data historis untuk estimasi
parameternya.

Mengapa agreement weighting?
----------------------------
Riset multi-agent menunjukkan "uniform trust" (pemberian bobot sama untuk
semua sumber) itu bahaya: sumber yang lemah/noisy ikut nyaring dengan bobot
penuh. Agen yang searah dengan mayoritas agen lain layak diberi bonus
bobot, karena kesepakatan silang antar agen adalah satu-satunya bukti yang
membedakan sinyal nyata dari noise.
"""

import math
from typing import Dict, List, Optional


# Batas z-score per agen. Sengaja dijaga: satu agen liar tidak boleh
# mendominasi ensemble sebelumshrinkage sempat bekerja.
MAX_AGENT_Z = 2.5

DIRECTION_LONG = "LONG"
DIRECTION_SHORT = "SHORT"
DIRECTION_NEUTRAL = "NEUTRAL"


def _sigmoid(z: float) -> float:
    """Sigmoid numerik stabil untuk z besar."""
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _normalize_z(direction: str, confidence: float) -> float:
    """
    Ubah verdict agen menjadi z-score bertanda dalam [-MAX_AGENT_Z, +MAX_AGENT_Z].

    `confidence` 0..1 dipetakan linier ke 0..MAX_AGENT_Z, lalu diberi tanda
    sesuai arah. Arah NEUTRAL selalu menghasilkan z = 0.
    """
    conf = max(0.0, min(1.0, float(confidence)))
    magnitude = MAX_AGENT_Z * conf
    if direction == DIRECTION_LONG:
        return magnitude
    if direction == DIRECTION_SHORT:
        return -magnitude
    return 0.0


def agreement_score(verdict: dict, peers: List[dict]) -> float:
    """
    Seberapa besar verdict ini searah dengan agen lain, dalam [0, 1].

    1.0 = semua agen lain searah. 0.0 = tidak ada yang searah.

    Agen dengan z = 0 (NETRAL) tidak dihitung sebagai bukti kecocokan maupun
    sebagai dissent — keduanya sama saja tidak informatif.
    """
    others = [p for p in peers if _normalize_z(p.get("direction"), p.get("confidence", 0.0)) != 0.0]
    if not others:
        return 0.0
    same = 0
    for p in others:
        z_p = _normalize_z(p.get("direction"), p.get("confidence", 0.0))
        if (z_p > 0) == (verdict["_z"] > 0):
            same += 1
    return same / len(others)


def aggregate(verdicts: List[dict], cfg) -> dict:
    """
    Gabungkan verdict agen menjadi satu probabilitas arah.

    Args:
        verdicts: daftar dict verdict dari agen spesialis. Setiap dict
                  minimally berisi `agent`, `direction`, `confidence`.
                  Kunci `abstained=True` akan diabaikan.
        cfg: EnsembleConfig (core.config).

    Returns:
        dict berisi prob_long, prob_short, direction, confidence, z_composite,
        agent_breakdown, n_agents, n_abstained, dan breakdown per agen.
    """
    all_verdicts = list(verdicts or [])

    # 1. Pisahkan yang abstain. Abstain berarti "tidak punya data", BUKAN
    #    "data netral" — membiarkan keduanya bercampur akan membuat agen
    #    yangbuta data terlihat seperti agen yang optimistis netral.
    active = [v for v in all_verdicts if not v.get("abstained", False)]
    n_abstained = len(all_verdicts) - len(active)

    # Normalisasi z-score sekali, dipakai untuk agreement maupun pooling.
    for v in active:
        v["_z"] = _normalize_z(v.get("direction", DIRECTION_NEUTRAL), v.get("confidence", 0.0))

    breakdown = [
        {
            "agent": v.get("agent", "?"),
            "direction": v.get("direction", DIRECTION_NEUTRAL),
            "confidence": round(float(v.get("confidence", 0.0)), 4),
            "z": round(float(v["_z"]), 4),
            "reasoning": v.get("reasoning", ""),
        }
        for v in active
    ]

    # 2. Tidak ada satu pun agen yang punya data -> netral murni.
    if not active:
        return {
            "prob_long": 0.5,
            "prob_short": 0.5,
            "direction": DIRECTION_NEUTRAL,
            "confidence": 0.0,
            "z_composite": 0.0,
            "agent_breakdown": [],
            "n_agents": 0,
            "n_abstained": n_abstained,
        }

    # 3. Bobot: dasar dari config, dikalikan bonus kesepakatan.
    numerator = 0.0
    denominator = 0.0
    for v in active:
        base = float(cfg.base_weight(v.get("agent", "")))
        if base <= 0.0:
            # Agen tidak dikenal atau nonaktif di config -> diabaikan, tapi
            # TETAP dihitung sebagai abstain supaya angkanya jujur.
            v["abstained"] = True
            continue
        bonus = 1.0 + float(cfg.agreement_bonus) * agreement_score(v, active)
        weight = base * bonus
        numerator += weight * v["_z"]
        denominator += weight

    if denominator <= 0.0:
        # Semua agen berbobot 0 — tidak ada sinyal sama sekali.
        return {
            "prob_long": 0.5,
            "prob_short": 0.5,
            "direction": DIRECTION_NEUTRAL,
            "confidence": 0.0,
            "z_composite": 0.0,
            "agent_breakdown": breakdown,
            "n_agents": len(breakdown),
            "n_abstained": n_abstained,
        }

    # 4. Pooling z-score tertimbang, lalu shrinkage.
    z = numerator / denominator
    z_shrunk = z * float(cfg.shrinkage_delta)

    prob_long = _sigmoid(z_shrunk)

    # 5. Clamp supaya tidak pernah 0 atau 1 absolut.
    lo = float(cfg.min_prob)
    prob_long = max(lo, min(1.0 - lo, prob_long))

    prob_short = 1.0 - prob_long
    confidence = abs(prob_long - 0.5) * 2.0

    if prob_long > 0.5:
        direction = DIRECTION_LONG
    elif prob_long < 0.5:
        direction = DIRECTION_SHORT
    else:
        direction = DIRECTION_NEUTRAL

    return {
        "prob_long": prob_long,
        "prob_short": prob_short,
        "direction": direction,
        "confidence": confidence,
        "z_composite": z,
        "z_shrunk": z_shrunk,
        "agent_breakdown": breakdown,
        "n_agents": len(breakdown),
        "n_abstained": n_abstained,
    }


def make_verdict(
    agent: str,
    symbol: str,
    direction: str,
    confidence: float,
    reasoning: str = "",
    factors: Optional[dict] = None,
    abstained: bool = False,
) -> dict:
    """
    Bangun verdict dengan bentuk seragam. Dipakai semua agen spesialis supaya
    aggregator tidak perlu tahu asal-usul tiap dict.
    """
    return {
        "agent": agent,
        "symbol": symbol,
        "direction": direction,
        "confidence": float(confidence),
        "reasoning": reasoning,
        "factors": factors or {},
        "abstained": bool(abstained),
    }


def abstain(agent: str, symbol: str, reason: str = "tidak ada data") -> dict:
    """Verdict abstain — agen tidak punya data, bukan netral."""
    return make_verdict(
        agent=agent,
        symbol=symbol,
        direction=DIRECTION_NEUTRAL,
        confidence=0.0,
        reasoning=reason,
        abstained=True,
    )
