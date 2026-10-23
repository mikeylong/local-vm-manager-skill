"""STDIO only: diagnostic output never enters the MCP stdout transport."""
from mcp.server import MCPServer
from .manager import Manager,ManagerError
from mcp.server.mcpserver.exceptions import ToolError,ResourceError
from functools import wraps
mcp=MCPServer('local-vm-manager',version='0.1.0',instructions='Owns its isolated Linux browser guests and one fixed reviewed Surfaces connected-observer workload. Capture pages, run selected web Playwright specs, or start that exact fixed workload; poll status, renew leases, finish. Evidence paths and SHA-256 survive owned runtime deletion. General native/macOS execution is unsupported.')

def manager():return Manager().init()
def expected_errors(fn):
    @wraps(fn)
    def call(*args,**kwargs):
        try:return fn(*args,**kwargs)
        except ManagerError as e:raise ToolError(str(e)) from None
    return call
@mcp.tool()
@expected_errors
def vm_start(label:str='browser',lease_seconds:int=120)->dict:
    """Create an owned Linux browser job. Returns immediately while provisioning. Client process death triggers cleanup; lease max is one hour."""
    return manager().start(label,lease_seconds)
@mcp.tool()
@expected_errors
def vm_run_playwright(source_dir:str,tests:list[str],expected_tests:int,label:str='playwright')->dict:
    """Freeze a bounded npm web project and run selected tests/browser/*.spec.ts inside a new Linux guest. Poll status and renew the lease every 30 seconds. Reports are verified before owned guest deletion. No native tests or host browser launches."""
    from . import playwright
    return playwright.start(manager(),source_dir,tests,expected_tests,label)
@mcp.tool()
@expected_errors
def vm_run_browser_suites(source_dir:str,profile:str,tests:list[str]|None=None,label:str='browser-suites')->dict:
    """Run existing allowlisted Surfaces or JudgmentKit Node browser entrypoints in Linux. Omit tests for the profile's complete maintained set. Poll and renew every 30 seconds. Missing prerequisites and test failures retain evidence and delete the owned guest; no native tests."""
    from . import suites
    return suites.start(manager(),source_dir,profile,tests,label)
@mcp.tool()
@expected_errors
def vm_run_surfaces_connected_observer(label:str='surfaces-connected-observer')->dict:
    """Run only the reviewed Surfaces connected-observer two-fixture workload. It exposes no source path or command arguments. Poll and renew every 30 seconds; success requires authenticated fixture evidence and verified deletion of the newly created Surfaces runtime."""
    from . import surfaces_connected
    return surfaces_connected.start(manager(),label)
@mcp.tool()
@expected_errors
def vm_renew(job_id:str,lease_seconds:int=120)->dict:
    """Renew a live agent's lease every 30 seconds, including while provisioning. Stops after the job's one-hour hard deadline. A live MCP server does not imply a live agent."""
    return manager().renew(job_id,lease_seconds)
@mcp.tool()
@expected_errors
def vm_status(job_id:str)->dict:
    """Read job phase, exported evidence hashes, and verified deletion status."""
    return manager().status(job_id)
@mcp.tool()
@expected_errors
def vm_capture(job_id:str,url:str='pilot')->dict:
    """Capture screenshot, rendered DOM, browser log and result in the Linux guest. URL must be HTTP(S), or pilot for the deterministic functional fixture. Poll vm_status for export."""
    return manager().capture(job_id,url)
@mcp.tool()
@expected_errors
def vm_finish(job_id:str)->dict:
    """Request export, stop, and deletion of this owned job only. Poll vm_status until complete/failed; cleanup_blocked is not success."""
    return manager().finish(job_id)
@mcp.tool()
@expected_errors
def vm_inventory()->dict:
    """Read only this manager's jobs and aggregate allocated storage. Does not enumerate external VMs."""
    return manager().inventory()
@mcp.resource('vm://{job_id}/evidence')
def evidence(job_id:str)->str:
    import json
    try:x=manager().status(job_id)
    except ManagerError as e:raise ResourceError(str(e)) from None
    return json.dumps({'directory':x['evidence_path'],'files':x['exports'],'verified':x.get('evidence_verified',False)})
if __name__=='__main__':mcp.run(transport='stdio')
