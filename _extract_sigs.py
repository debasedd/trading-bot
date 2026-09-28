import ast, sys

def walk(path):
    src = open(path, encoding='utf-8').read()
    lines = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print("!! SYNTAXERROR %s: %s" % (path, e))
        return
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(src, node)
            head = seg.split('\n')[0].rstrip()
            if head.endswith(':'):
                head = head[:-1]
            decs = [ast.get_source_segment(src, d) for d in node.decorator_list]
            out.append((node.lineno, 'FUNC', head, decs))
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            decs = [ast.get_source_segment(src, d) for d in node.decorator_list]
            out.append((node.lineno, 'CLASS', "class %s(%s)" % (node.name, bases), decs))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    seg = ast.get_source_segment(src, sub)
                    head = seg.split('\n')[0].rstrip()
                    if head.endswith(':'):
                        head = head[:-1]
                    out.append((sub.lineno, '  METH', head, []))
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    v = " = %s" % ast.unparse(sub.value) if sub.value else ""
                    out.append((sub.lineno, '  ANNF', "%s: %s%s" % (sub.target.id, ast.unparse(sub.annotation), v), []))
                elif isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            out.append((sub.lineno, '  ASGN', "%s = %s" % (t.id, ast.unparse(sub.value)), []))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            v = " = %s" % ast.unparse(node.value) if node.value else ""
            out.append((node.lineno, 'CONST', "%s: %s%s" % (node.target.id, ast.unparse(node.annotation), v), []))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.append((node.lineno, 'CONST', "%s = %s" % (t.id, ast.unparse(node.value)), []))
    out.sort(key=lambda x: x[0])
    print("")
    print("########## %s  (%d lines) ##########" % (path, len(lines)))
    for ln, kind, txt, decs in out:
        for d in decs:
            print("%5d  %-6s @%s" % (ln, kind, d))
        print("%5d  %-6s %s" % (ln, kind, txt))

for p in sys.argv[1:]:
    walk(p)
