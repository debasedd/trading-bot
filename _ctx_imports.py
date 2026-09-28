import ast, pathlib, collections, sys

ROOT = pathlib.Path(__file__).resolve().parent
PKGS = {'core', 'agents', 'analysis', 'trading', 'data', 'database', 'dashboard', 'ml'}

edges = collections.defaultdict(set)
deferred = collections.defaultdict(set)
files = [p for p in ROOT.rglob('*.py')
         if 'build' not in p.parts and 'data_store' not in p.parts
         and '__pycache__' not in p.parts and p.name not in ('_ctx_imports.py',)]

def modname(p):
    rel = p.relative_to(ROOT)
    parts = list(rel.parts)
    if parts[-1] == '__init__.py':
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return '.'.join(parts)

for p in files:
    mod = modname(p)
    if mod.split('.')[0] not in PKGS:
        continue
    try:
        tree = ast.parse(p.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        continue

    def resolve(n):
        out = []
        if isinstance(n, ast.ImportFrom):
            if n.level and n.module is None:
                return []
            if n.module and n.module.split('.')[0] in PKGS:
                out.append('.'.join(n.module.split('.')[:2]))
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split('.')[0] in PKGS:
                    out.append('.'.join(a.name.split('.')[:2]))
        return out

    # module level
    for n in tree.body:
        for t in resolve(n):
            edges[mod].add(t)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(n):
                if sub is not n:
                    for t in resolve(sub):
                        deferred[mod].add(t)
    # class level bodies
    for n in tree.body:
        if isinstance(n, ast.ClassDef):
            for sub in ast.walk(n):
                for t in resolve(sub):
                    deferred[mod].add(t)

print('=== MODULE-LEVEL EDGES ===')
for k in sorted(edges):
    print(k, '->', sorted(edges[k] - {k}))
print()
print('=== DEFERRED (function/class-local) EDGES ===')
for k in sorted(deferred):
    d = sorted(deferred[k] - edges[k] - {k})
    if d:
        print(k, '->', d)
print()
print('=== FAN-IN (module-level) ===')
fan = collections.Counter()
for k, vs in edges.items():
    for v in vs:
        fan[v] += 1
for k, c in fan.most_common(20):
    print(c, k)
