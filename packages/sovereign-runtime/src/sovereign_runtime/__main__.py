import sys
import click

@click.command()
def main():
    """Sovereign SDK Local Runtime Node."""
    click.echo("====================================================")
    click.echo("🟢 Sovereign Node initialization sequence successful.")
    click.echo(f"Running locally on Python {sys.version.split()[0]}")
    click.echo("====================================================")


if __name__ == "__main__":
    main()