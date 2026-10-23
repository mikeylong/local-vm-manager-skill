import argparse,asyncio,json,pathlib
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=pathlib.Path(__file__).resolve().parent.parent
async def main():
    async with stdio_client(StdioServerParameters(command=str(ROOT/'bin/lvm-mcp'))) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize();t=await s.list_tools()
            result=await s.call_tool('vm_inventory',{})
            data=result.structured_content or json.loads(result.content[0].text)
            assert not result.is_error and 'jobs' in data
            done=next((x for x in data['jobs'] if x['state']=='complete'),None)
            if done:
                result=await s.call_tool('vm_status',{'job_id':done['job_id']})
                receipt=result.structured_content or json.loads(result.content[0].text)
                assert not result.is_error and receipt['vm_deleted']
            bad=await s.call_tool('vm_status',{'job_id':'minerva'});assert bad.is_error
            x={'transport':'stdio','sdk':'2.3.0','tool_names':[x.name for x in t.tools],
               'inventory_read':True,'completed_receipt_read':bool(done),'external_vm_name_rejected':True}
            bad=await s.call_tool('vm_run_playwright',{'source_dir':str(ROOT),'tests':['ios/Native.spec.ts'],'expected_tests':1});assert bad.is_error
            x['native_spec_rejected']=True
            assert 'vm_run_browser_suites' in x['tool_names']
            tool=next(t for t in t.tools if t.name=='vm_run_browser_suites')
            x['browser_suite_schema']=tool.input_schema
            bad=await s.call_tool('vm_run_browser_suites',{'source_dir':str(ROOT),'profile':'surfaces','tests':['ios/Native.test.ts']});assert bad.is_error
            x['native_node_suite_rejected']=True
            assert 'vm_run_surfaces_connected_observer' in x['tool_names']
            surfaces=next(t for t in t.tools if t.name=='vm_run_surfaces_connected_observer')
            properties=surfaces.input_schema.get('properties',{})
            assert set(properties)<= {'label'} and 'source_dir' not in properties and 'command' not in properties
            x['surfaces_connected_observer_schema']=surfaces.input_schema
            assert 'vm_run_playwright' in x['tool_names']
            output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(x,indent=2)+'\n');print(json.dumps(x))
parser=argparse.ArgumentParser();parser.add_argument('--output',type=pathlib.Path,default=ROOT/'verification/mcp-protocol-current.json');output=parser.parse_args().output
asyncio.run(main())
