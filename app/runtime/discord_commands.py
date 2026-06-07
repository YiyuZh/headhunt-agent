import argparse

from app.core.config import get_settings
from app.discord.commands import DiscordCommandRegistry
from app.schemas.discord_commands import DiscordCommandRegisterResponse


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Register Discord slash commands.")
    parser.add_argument(
        "--guild-id",
        action="append",
        dest="guild_ids",
        help="Guild id to register commands into. Can be passed multiple times.",
    )
    parser.add_argument(
        "--global",
        action="store_true",
        dest="include_global",
        help="Also register global application commands.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print command names without calling Discord.",
    )
    args = parser.parse_args(argv)

    registry = DiscordCommandRegistry(settings=get_settings())
    response = _register_or_preview(
        registry=registry,
        guild_ids=args.guild_ids,
        include_global=args.include_global,
        dry_run=args.dry_run,
    )
    print(response.model_dump_json(indent=2))


def _register_or_preview(
    *,
    registry: DiscordCommandRegistry,
    guild_ids: list[str] | None,
    include_global: bool,
    dry_run: bool,
) -> DiscordCommandRegisterResponse:
    command_names = registry.command_names()
    if dry_run:
        return DiscordCommandRegisterResponse(
            dry_run=True,
            command_names=command_names,
            registered=[],
            message="Dry run only; no Discord API request was sent.",
        )
    registered = registry.register_commands(
        guild_ids=guild_ids,
        include_global=include_global,
    )
    return DiscordCommandRegisterResponse(
        dry_run=False,
        command_names=command_names,
        registered=registered,
        message="Discord commands registered.",
    )


if __name__ == "__main__":
    main()
