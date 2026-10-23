"""Real MCP client used as the killable agent in integration verification."""
import asyncio,json,pathlib
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=pathlib.Path(__file__).resolve().parent.parent
async def main():
    async with stdio_client(StdioServerParameters(command=str(ROOT/'bin/lvm-mcp'))) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize()
            async def call(name,args):
                result=await s.call_tool(name,args)
                if result.is_error:raise RuntimeError(str(result.content))
                return result.structured_content or json.loads(result.content[0].text)
            x=await call('vm_start',{'label':'mcp-agent-crash-pilot','lease_seconds':120})
            job=x['job_id'];print(json.dumps({'event':'started','job_id':job}),flush=True)
            while True:
                await call('vm_renew',{'job_id':job})
                x=await call('vm_status',{'job_id':job})
                if x['state']=='ready':break
                if x['state'] in ('failed','cleanup_blocked'):raise RuntimeError(str(x))
                await asyncio.sleep(3)
            result=await call('vm_capture',{'job_id':job,'url':'pilot'})
            print(json.dumps({'event':'capture_requested',**result}),flush=True)
            # Parent integration harness kills this client during supervisor export.
            while True:
                await call('vm_renew',{'job_id':job});await asyncio.sleep(10)
asyncio.run(main())
