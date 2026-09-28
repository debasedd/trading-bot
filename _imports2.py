import ast, os, sys

PKGS = {'core', 'agents', 'analysis', 'trading', 'data', 'database', 'dashboard', 'ml'}
files = []
for pk in sorted(PKGS):
    for dp, dn, fn in os.walk(pk):
        if '__pycache__' in dp:
            continue
        for f in sorted(fn):
            if f.endswith('.py'):
                files.append(os.path.join(dp, f))
files.append('run.py')

for p in files:
    src = open(p, encoding='utf-8').read()
    try:
        t = ast.parse(src)
    except SyntaxError:
        continue
    # find enclosing function names for each node
    stack = []

    def visit(node, ctx):
        for child in ast.iter_child_nodes(node):
            newctx = ctx
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                newctx = child.name
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                tg = None
                if isinstance(child, ast.Import):
                    for a in child.names:
                        if a.name.split('.')[0] in PKGS:
                            tg = a.name
                            break
                else:
                    if not child.level and child.module and child.module.split('.')[0] in PKGS:
                        tg = child.module
                if tg and getattr(child, 'col_offset', 0) > 0:
                    print('%-44s @%-5d in %-22s %s' % (p, child.lineno, ctx, tg))
            visit(child, newctx)

    visit(t, '<module>')
