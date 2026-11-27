"""Read-only Aura acceptance test; no embeddings or synthetic conversation writes.

Run in the console with an existing populated Code project as argv[1].
"""
import json
import sys
import uuid
from relate_tap.intent_recall import IntentRecall


def main():
    project=sys.argv[1]
    backend=IntentRecall()
    try:
        recall=backend.request('recall',{'context_kind':'Code','project':project,
            'source_agent':'codex','query':'design decisions','limit':10,'max_chars':24000})
        assert recall['state']=='ready' and recall['origin']=='aura'
        assert recall['sessions'] and recall['matches']
        assert all(row['agent']=='codex' for row in recall['matches'])
        key=recall['sessions'][0]['id']
        session=backend.request('session',{'context_kind':'Code','project':project,'session':key,'limit':4})
        assert session['state']=='ready' and session['messages']
        turn=recall['matches'][0]['turn_id']
        evidence=backend.request('evidence',{'context_kind':'Code','project':project,'turn_id':turn})
        assert evidence['state']=='ready' and evidence['evidence']
        other='absent-coding-scope-'+uuid.uuid4().hex
        wrong=backend.request('session',{'context_kind':'Code','project':other,'session':key})
        assert wrong['state']=='empty' and not wrong['messages']
        print(json.dumps({'status':'passed','origin':'aura','project':project,
            'codex_sessions':len(recall['sessions']),'matched_turns':len(recall['matches']),
            'expanded_messages':len(session['messages']),'turn_evidence':len(evidence['evidence']),
            'cross_project_blocked':True,'embedding_calls':0}))
    finally:backend.close()


if __name__=='__main__':main()
