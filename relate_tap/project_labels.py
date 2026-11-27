"""Additive classification of existing projects using an explicit inventory.

No inferred classifications from agent names or project name patterns. Existing
sessions in mixed projects remain unclassified unless already labeled.
"""


def statements(code_projects=(), chat_projects=()):
    for kind, projects in (('Code', code_projects), ('Chat', chat_projects)):
        projects = list(dict.fromkeys(projects))
        if any(not isinstance(p, str) or not p.strip() for p in projects):
            raise ValueError('Expected non-empty project names')
        yield f'''MATCH(p:ContextProject) WHERE p.name IN $input.projects
            SET p:{kind} RETURN count(p) AS projects''', {'projects': projects}
    for kind, opposite in (('Code', 'Chat'), ('Chat', 'Code')):
        yield f'''MATCH(p:ContextProject:{kind})-[:HAS_SESSION]->(s:ContextSession)
            WHERE NOT p:{opposite} AND NOT s:{opposite}
            SET s:{kind} RETURN count(DISTINCT s) AS sessions''', {}


def backfill(run, code_projects=(), chat_projects=()):
    return [run(query, params, read_only=False)
            for query, params in statements(code_projects, chat_projects)]
