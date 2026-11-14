"""Render the instance user-data template the way Terraform's templatefile does."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USER_DATA_TEMPLATE = ROOT / "infra" / "templates" / "user-data.sh.tftpl"

SAMPLE_VARIABLES = {
    "bucket": "ai-image-studio-example",
    "region": "us-east-1",
    "data_volume_id": "vol-0123456789abcdef0",
    "idle_minutes": "30",
    "failsafe_minutes": "240",
}


def render_terraform_template(source: str, variables: dict[str, str]) -> str:
    sentinel = "\x00DOLLAR\x00"
    rendered = source.replace("$${", sentinel)
    for key, value in variables.items():
        rendered = rendered.replace("${" + key + "}", value)
    unresolved = [line for line in rendered.splitlines() if "${" in line]
    if unresolved:
        raise ValueError("unresolved terraform interpolation:\n" + "\n".join(unresolved))
    return rendered.replace(sentinel, "${")


def render_user_data(variables: dict[str, str] | None = None) -> str:
    source = USER_DATA_TEMPLATE.read_text(encoding="utf-8")
    return render_terraform_template(source, variables or SAMPLE_VARIABLES)
