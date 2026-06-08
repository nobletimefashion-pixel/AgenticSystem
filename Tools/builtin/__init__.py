from Tools.builtin.edit_file import EditTool
from Tools.builtin.glob import GlobTool
from Tools.builtin.grep import GrepTool
from Tools.builtin.list_dir import ListDirTool
from Tools.builtin.memory import MemoryTool
from Tools.builtin.rag import RAGTool
from Tools.builtin.read_file import ReadFileTool
from Tools.builtin.shell import ShellTool
from Tools.builtin.todo import TodosTool
from Tools.builtin.web_fetch import WebFetchTool
from Tools.builtin.web_search import WebSearchTool
from Tools.builtin.write_file import WriteFileTool
from Tools.builtin.browerser import BrowserTool
from Tools.builtin.domain_osint import DomainOSINTTool
from Tools.builtin.email_osint import EmailOSINTTool
from Tools.builtin.git import GitTool
from Tools.builtin.ip_osint import IPOSINTTool
from Tools.builtin.username_osint import UsernameOSINTTool
from Tools.builtin.phone_osint import PhoneOSINTTool
from Tools.builtin.video_generate import VideoCreatorTool
from Tools.builtin.whois_osint import WHOISOSINTTool
from Tools.builtin.censys_osint import CensysOSINTTool
from Tools.builtin.web_security_audit import WebSecurityAuditTool
from Tools.builtin.os_security_monitor import OSSecurityMonitorTool
from Tools.builtin.report_generator import ReportGeneratorTool
from Tools.builtin.software_architect import SoftwareArchitectTool

__all__ = ["ReadFileTool","WriteFileTool","EditTool","ShellTool","ListDirTool","GrepTool","GlobTool","WebSearchTool","WebFetchTool","TodosTool","MemoryTool","RagTool","BrowserTool","DomainOSINTTool","EmailOSINTTool","GitTool","IPOSINTTool","UsernameOSINTTool","PhoneOSINTTool","VideoCreatorTool","WHOISOSINTTool","CensysOSINTTool","WebSecurityAuditTool","OSSecurityMonitorTool","ReportGeneratorTool","SoftwareArchitectTool"]

def get_all_builtin_tools() -> list[type]:
    return [
        ReadFileTool,
        WriteFileTool,
        EditTool,
        ShellTool,
        ListDirTool,
        GrepTool,
        GlobTool,
        WebSearchTool,
        WebFetchTool,
        TodosTool,
        MemoryTool,
        RAGTool,
        BrowserTool,
        DomainOSINTTool,
        EmailOSINTTool,
        GitTool,
        IPOSINTTool,
        UsernameOSINTTool,
        PhoneOSINTTool,
        VideoCreatorTool,
        WHOISOSINTTool,
        CensysOSINTTool,
        WebSecurityAuditTool,
        OSSecurityMonitorTool,
        ReportGeneratorTool,
        SoftwareArchitectTool,
        #now whenever we want to add a tool will register it here
    ]