#!/usr/bin/env python3

import json
import logging
import os
import re
import subprocess
import sys
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import TypedDict

import click
import requests_cache

PUZZLE_URL = "https://www.nytimes.com/puzzles/sudoku/hard"

LOGGER = logging.getLogger(__name__)


class Difficulty(Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    ALL = "all"


class PuzzleData(TypedDict):
    hints: list
    puzzle: list
    solution: list


class Puzzle(TypedDict):
    puzzle_id: int
    version: int
    day_of_week: str
    difficulty: str
    print_date: str
    published: str
    puzzle_data: PuzzleData


class ScrapeData(TypedDict):
    displayDate: str
    easy: Puzzle
    medium: Puzzle
    hard: Puzzle


class ScrapeError(Exception):
    def __init__(self):
        super().__init__()


def scrape_puzzle(
    session: requests_cache.CachedSession, refresh_cache: bool
) -> ScrapeData:
    response = session.get(PUZZLE_URL, refresh=refresh_cache)
    pattern = r'<script type="text\/javascript">window\.gameData = (.+)<\/script><\/div><div id="portal-editorial-content">'
    match = re.search(pattern, response.text)
    if match:
        data: ScrapeData = json.loads(match.group(1))
        return data
    raise ScrapeError


def format_pdf(
    puzzle_data: ScrapeData,
    large_print: bool,
    left_handed: bool,
    solution: bool,
    difficulty: Enum,
) -> None:

    # Variations of the puzzle (left-handed or large-print) are retrieved via
    # URL query string parameters. These do not apply to the solution PDF.

    if difficulty is Difficulty.ALL:
        puzzles = (puzzle_data["hard"], puzzle_data["medium"], puzzle_data["easy"])
    else:
        puzzles = (puzzle_data[difficulty.value],)

    for puz_data in puzzles:
        puz = puz_data["puzzle_data"]["puzzle"]
        sdk_output = ""
        for i in range(len(puz)):
            if i % 9 == 0:
                sdk_output += "\n"
            if puz[i] == 0:
                sdk_output += "."
            else:
                sdk_output += str(puz[i])
        print(sdk_output)


def write_pdf(data: bytes, path: os.PathLike) -> None:
    LOGGER.debug("Writing to file [path=%s]: %s", path, data[:32])
    with open(path, "wb") as pdf:
        pdf.write(data)
    print(f"Wrote {len(data)} bytes to {path}")


def print_file(path: os.PathLike) -> None:
    LOGGER.debug("Printing for OS: %s", os.name)
    if os.name == "nt":
        try:
            LOGGER.debug("Attempting os.startfile with operation=print [path=%s]", path)
            # This function is only available on Windows
            os.startfile(path, "print")
        except OSError as e:
            if e.winerror == 1155:
                LOGGER.debug(
                    "Printing via os.startfile failed. Offering to open via default app without operation."
                )
                print(
                    "No application is registered to handle printing PDFs, make sure that an application is registered and that it supports printing",
                    file=sys.stderr,
                )
                print(
                    "This change can be made in Windows Settings. Note that you may have to end all tasks of the current default in Task Manager.",
                    file=sys.stderr,
                )
                if click.confirm(
                    "Would you like to try opening in the default app without automatically printing?"
                ):
                    try:
                        os.startfile(path)
                    except OSError as e:
                        print(
                            "The file failed to open in the default app.",
                            file=sys.stderr,
                        )
                        print(str(e), file=sys.stderr)
            else:
                print(str(e), file=sys.stderr)
            print()

    else:
        try:
            LOGGER.debug("Attempting to print via lp [path=%s]", path)
            result = subprocess.run(["lp", str(path)], capture_output=True, check=False)
            print(result.stdout)
        except subprocess.SubprocessError as e:
            print(str(e), file=sys.stderr)


@click.command("nyt-download")
@click.option(
    "--large-print/--no-large-print",
    default=False,
    show_default=True,
    help="Whether to fetch the large-print puzzle variant",
)
@click.option(
    "--left-handed/--no-left-handed",
    default=False,
    show_default=True,
    help="Whether to fetch the left-handed puzzle variant",
)
@click.option(
    "--solution/--no-solution",
    default=True,
    show_default=True,
    help="Whether to also include the solution in the download/print",
)
@click.option(
    "--out-dir",
    "-o",
    type=click.Path(exists=False, file_okay=False),
    default="out",
    show_default=True,
    help="The directory where the output PDFs should be placed",
)
@click.option(
    "--print/--no-print",
    "-p",
    "do_print",
    default=False,
    show_default=True,
    help="Whether to send the PDFs to the default printer automatically",
)
@click.option(
    "--verbose/--no-verbose",
    "-v",
    default=False,
    show_default=True,
    help="Enable verbose logging",
)
@click.option(
    "--refresh/--no-refresh",
    "-r",
    default=False,
    show_default=True,
    help="Whether to refresh the cached puzzle response",
)
@click.option(
    "--difficulty",
    "-d",
    type=click.Choice(Difficulty, case_sensitive=False),
    default=Difficulty.ALL,
    show_default=True,
    help="The difficulty of puzzle to print (easy, medium, hard, all)",
)
def main(
    large_print: bool,
    left_handed: bool,
    solution: bool,
    out_dir: Path,
    do_print: bool,
    verbose: bool,
    refresh: bool,
    difficulty: Difficulty,
):
    logging.basicConfig(
        format="%(asctime)s:%(levelname)s: %(message)s",
        level=logging.DEBUG if verbose else logging.INFO,
    )

    session = requests_cache.CachedSession("sudoku", expire_after=timedelta(hours=1))

    try:
        data = scrape_puzzle(session, refresh)
        files = format_pdf(data, large_print, left_handed, solution, difficulty)
    except Exception:
        print("Some sort of error", sys.stdout)
    return
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    puzzle_filename = Path(out_dir, f"{determine_date(puzzle_date)}.pdf")
    soln_filename = Path(out_dir, f"{determine_date(puzzle_date)}.soln.pdf")
    write_pdf(files.puzzle, puzzle_filename)
    if solution and files.solution:
        write_pdf(files.solution, soln_filename)

    if do_print:
        print(f"Sending {puzzle_filename} to default printer")
        print_file(puzzle_filename)
        if solution:
            print(f"Sending {soln_filename} to default printer")
            print_file(soln_filename)


if __name__ == "__main__":
    main()
