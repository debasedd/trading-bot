import asyncio, json, websockets
URL="wss://api.hyperliquid.xyz/ws"
async def run(msgs,label,readsec=6):
    print("====",label)
    try:
        async with websockets.connect(URL, ping_interval=None) as ws:
            for m in msgs: await ws.send(json.dumps(m))
            t=asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time()-t<readsec:
                try: raw=await asyncio.wait_for(ws.recv(),timeout=3)
                except asyncio.TimeoutError: print("  [no more]"); break
                try: m=json.loads(raw)
                except Exception: print("  NONJSON",repr(raw)[:120]); continue
                print("  ",json.dumps(m)[:400])
    except Exception as e: print("  EXC",type(e).__name__,e)
async def main():
    await run([{"method":"subscribe","subscription":{"type":"l2Book","coin":"BTC","nSigFigs":5,"fast":True}}],"fast book",5)
    await run([{"method":"subscribe","subscription":{"type":"l2Book","coin":"NOTACOIN"}}],"bad coin",5)
    await run([{"method":"subscribe","subscription":{"type":"bogusType"}}],"bad type",5)
    await run([{"method":"subscribe","subscription":{"type":"activeAssetCtx","coin":"@1"}}],"spot @1 ctx",5)
    await run([{"method":"subscribe","subscription":{"type":"activeAssetCtx","coin":"PURR"}}],"spot PURR ctx",5)
    await run([{"method":"candle","subscription":{"type":"allMids"}}],"bad method",4)
    await run([{"method":"subscribe","subscription":{"type":"candle","coin":"ETH","interval":"1m"}}],"candle eth",4)
asyncio.run(main())
