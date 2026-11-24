"""ATS port package: base interface + adapters (local folder now; iCIMS/Greenhouse/Lever later, config-switched)."""

from __future__ import annotations

from resume_screener.ats.base import ATSPort
from resume_screener.config import AppConfig, ConfigError


def get_ats(config: AppConfig, resumes_override: str | None = None) -> ATSPort:
    """Adapter factory — the config switch real ATS adapters plug into (§12).

    resumes_override: explicit folder (the CLI's --resumes flag); when unset the
    local adapter reads each requisition's own batch (data/roles/<slug>/resumes/).
    """
    if config.ats.provider == "local":
        from resume_screener.ats.local_folder import LocalFolderATS

        return LocalFolderATS(
            jd_dir=config.ats.jd_dir,
            resumes_dir=config.ats.resumes_dir,
            default_job_location=config.policy.default_jurisdiction,
            roles_dir=config.ats.roles_dir,
            resumes_override=resumes_override,
        )
    raise ConfigError(
        f"Unknown ATS provider '{config.ats.provider}' — only 'local' exists in the PoC "
        "(iCIMS/Greenhouse/Lever are production adapters)."
    )
