set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Load .env so MILVUS_* variables are available for envsubst and set-credentials
set -a; source .env; set +a

orchestrate tools import -k python -f tools/python/business_tools.py

orchestrate connections import -f connections/milvus.yaml
orchestrate connections set-credentials \
  --app-id milvus_kb \
  --environment draft \
  --username "$MILVUS_USER" \
  --password "$MILVUS_PASSWORD"

orchestrate knowledge-bases import -f knowledge_base/kb.yaml
orchestrate knowledge-bases status -n scenario_kb

orchestrate agents import -f agents/supervisor_agent.yaml
orchestrate agents import -f agents/maintenance_specialist.yaml
orchestrate agents import -f agents/documentation_specialist.yaml
orchestrate agents import -f agents/action_specialist.yaml


echo "Imported. Test in the wxO SaaS chat/preview UI with agent: scenario_supervisor"
