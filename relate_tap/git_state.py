"""Observed Git anchors, scoped to the repository actually being recorded."""
import os
from pathlib import Path
import re
import subprocess

from .project_identity import discover_repository

FIELDS = ('git_commit','git_branch','git_dirty')


def capture_git(project, kind, args, directory=None):
    state = {field:None for field in FIELDS}
    state['git_source'] = 'unavailable'
    if any(field in args for field in FIELDS):
        commit,branch,dirty = (args.get(field) for field in FIELDS)
        if commit is not None and (not isinstance(commit,str) or not re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}',commit)):
            raise ValueError('git_commit must be a full Git SHA')
        if branch is not None and (not isinstance(branch,str) or not branch or len(branch)>200 or any(ord(c)<32 for c in branch)):
            raise ValueError('Invalid git_branch')
        if dirty is not None and type(dirty) is not bool:
            raise ValueError('git_dirty must be a boolean')
        return {**dict(zip(FIELDS,(commit.lower() if commit else None,branch,dirty))),
                'git_source':'client_reported' if any(v is not None for v in (commit,branch,dirty)) else 'unavailable'}
    directory = Path(directory or Path.cwd()).resolve()
    if kind != 'Code' or discover_repository(directory) != project:
        return state
    def git(*args):
        result = subprocess.run(['git','-C',str(directory),*args],capture_output=True,
                                timeout=5,env={**os.environ,'GIT_OPTIONAL_LOCKS':'0'})
        if result.returncode:
            raise ValueError('Git state unavailable')
        return result.stdout
    try:
        commit=git('rev-parse','HEAD').decode().strip()
        branch=git('rev-parse','--abbrev-ref','HEAD').decode().strip()
        dirty=bool(git('status','--porcelain=v1','-z'))
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',commit):
            return state
        return dict(zip((*FIELDS,'git_source'),(commit,branch,dirty,'server_observed')))
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return state


def freshness(commit, dirty):
    if not commit:
        return 'unknown'
    return 'working_tree_modified' if dirty else 'requires_live_git_verification' if dirty is False else 'working_tree_unknown'
