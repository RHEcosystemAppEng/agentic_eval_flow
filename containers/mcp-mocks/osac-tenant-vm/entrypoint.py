import json,os
from evals.tenant_vm.mock_server.server import Audit,build_fixture,create_server
class StdoutAudit(Audit):
    def finish(self,event,outcome):
        super().finish(event,outcome)
        print(json.dumps({"event":"tool_call",**event}),flush=True)
fixture=os.environ['OSAC_FIXTURE']
data=build_fixture(fixture)
server=create_server(data,StdoutAudit(fixture,None,data),"http://managed-mock:8000/mcp")
server.run(transport="streamable-http",host="0.0.0.0",port=8000)
