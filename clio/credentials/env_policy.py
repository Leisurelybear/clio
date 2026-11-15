"""Environment write policy shared by credential and compatibility APIs."""

_PROJECT_ENV_NAMES = {
    "CLIO_LANG",
    "CLIO_FFMPEG_DIR",
    "FFMPEG_HOME",
    "HF_ENDPOINT",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "MKL_NUM_THREADS",
    "OMP_NUM_THREADS",
}
# These variables can change executable/module resolution or inject code into
# child processes after the saved .env is loaded.
_BLOCKED_ENV_NAMES = {
    "BASH_ENV",
    "BASHOPTS",
    "BROWSER",
    "CDPATH",
    "CLASSPATH",
    "COMSPEC",
    "EDITOR",
    "ENV",
    "GIT_ASKPASS",
    "GIT_EXEC_PATH",
    "GIT_PROXY_COMMAND",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "IFS",
    "JAVA_TOOL_OPTIONS",
    "JDK_JAVA_OPTIONS",
    "LESSOPEN",
    "LESSCLOSE",
    "MANPAGER",
    "NODE_OPTIONS",
    "NODE_PATH",
    "PAGER",
    "PATH",
    "PATHEXT",
    "PERL5LIB",
    "PERL5OPT",
    "PROMPT_COMMAND",
    "PYTHONBREAKPOINT",
    "PYTHONHOME",
    "PYTHONINSPECT",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONUSERBASE",
    "RUBYLIB",
    "RUBYOPT",
    "SHELL",
    "SHELLOPTS",
    "SSH_ASKPASS",
    "SYSTEMROOT",
    "VISUAL",
    "WINDIR",
    "_JAVA_OPTIONS",
}
_BLOCKED_ENV_PREFIXES = ("DYLD_", "GIT_CONFIG_", "LD_")


def _is_blocked_env_name(name: str) -> bool:
    normalized = name.strip().upper()
    if normalized in _PROJECT_ENV_NAMES:
        return False
    return normalized in _BLOCKED_ENV_NAMES or normalized.startswith(_BLOCKED_ENV_PREFIXES)
