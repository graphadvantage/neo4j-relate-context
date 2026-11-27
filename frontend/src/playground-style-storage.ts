import { isGraphStyleRule, type GraphStyleRule } from '@neo4j-ndl/react-graph';
import { reportConsoleError } from './diagnostics';

const storageKey='relate-console:graph-styles:v2:global';
const legacyPrefix='relate-console:graph-styles:v1:';

function parseRules(saved:string):GraphStyleRule[] {
  const rules:unknown=JSON.parse(saved);
  if(!Array.isArray(rules) || !rules.every(isGraphStyleRule)) throw Error('Saved graph styles are invalid. Using defaults.');
  return rules;
}

export function loadGraphStyles():GraphStyleRule[] {
  try {
    const saved=localStorage.getItem(storageKey);
    if(saved!==null) return parseRules(saved);
    // Merge existing source settings once. Preserve original keys as backups;
    // deterministic key order resolves conflicting settings for the same label.
    const merged=new Map<string,GraphStyleRule>();
    const keys=Object.keys(localStorage).filter(key=>key.startsWith(legacyPrefix)).sort();
    for(const key of keys) {
      try {
        for(const rule of parseRules(localStorage.getItem(key) ?? '[]')) {
          const target=JSON.stringify([rule.match,rule.where]);
          const previous=merged.get(target);
          merged.set(target,{...rule,apply:{...previous?.apply,...rule.apply}});
        }
      } catch(error) {reportConsoleError('Playground styling',(error as Error).message);}
    }
    const rules=[...merged.values()].sort((a,b)=>{
      const specific=(rule:GraphStyleRule)=>'label' in rule.match?rule.match.label!==null:'reltype' in rule.match?rule.match.reltype!==null:true;
      return Number(specific(a))-Number(specific(b));
    });
    saveGraphStyles(rules);
    return rules;
  } catch(error) {
    reportConsoleError('Playground styling',(error as Error).message);
    return [];
  }
}

export function saveGraphStyles(rules:GraphStyleRule[]) {
  try {
    // Keep an empty global value after Reset so legacy styles stay retired.
    localStorage.setItem(storageKey,JSON.stringify(rules));
  } catch {
    reportConsoleError('Playground styling','Browser storage is unavailable. Style changes will last only for this page session.');
  }
}
