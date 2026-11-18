#!/usr/bin/env python3

import json
import logging
import os
import re
import subprocess
import sys
from datetime import timedelta, datetime
from enum import Enum
from pathlib import Path
from typing import TypedDict
import tempfile
import matplotlib.pyplot as plt
from fpdf import FPDF, Align

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


def remove_stale(path: Path) -> None:
    for item in path.iterdir():
        if item.is_file():
            item.unlink()


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


def generate_board_from_puzzle_data(puzzle_data: list) -> list:
    puzzle_data_none = [None if x == 0 else x for x in puzzle_data]
    board = []
    for i in range(0, len(puzzle_data_none), 9):
        board.append(puzzle_data_none[i : i + 9])
    return board


def generate_puzzle_image(puzzle: Puzzle, out_path: str, solution: bool = False):
    if solution:
        board = generate_board_from_puzzle_data(puzzle["puzzle_data"]["solution"])
    else:
        board = generate_board_from_puzzle_data(puzzle["puzzle_data"]["puzzle"])
    _, ax = plt.subplots(figsize=(6, 6))
    plt.title(
        f"{datetime.strptime(puzzle['print_date'], '%Y-%m-%d').strftime('%A, %B %d, %Y')}  --  {puzzle['difficulty']}"
    )
    ax.set_xlim(0, 9)
    ax.set_ylim(0, 9)
    ax.invert_yaxis()  # Flip y-axis so (0,0) is at the top-left

    # Draw grid lines
    for i in range(10):
        lw = 2.5 if i % 3 == 0 else 0.8
        ax.axhline(i, color="black", linewidth=lw)
        ax.axvline(i, color="black", linewidth=lw)

    # Fill in numbers
    for r in range(9):
        for c in range(9):
            val = board[r][c]
            if val is not None:
                ax.text(
                    c + 0.5,
                    r + 0.5,
                    str(val),
                    fontsize=18,
                    ha="center",
                    va="center",
                    fontweight="bold",
                )

    ax.axis("off")
    plt.savefig(out_path, bbox_inches="tight", dpi=300)
    plt.close()


def format_pdf(
    scrape_data: ScrapeData,
    large_print: bool,
    left_handed: bool,
    solution: bool,
    difficulty: Enum,
) -> None:

    # Variations of the puzzle (left-handed or large-print) are retrieved via
    # URL query string parameters. These do not apply to the solution PDF.

    if difficulty is Difficulty.ALL:
        puzzles = (scrape_data["hard"], scrape_data["medium"], scrape_data["easy"])
    else:
        puzzles = (scrape_data[difficulty.value],)

    remove_stale(Path("./output"))
    for puz_data in puzzles:
        generate_puzzle_image(puz_data, f"output/{puz_data['difficulty']}.png")
        # puz = puz_data["puzzle_data"]["puzzle"]
        # sdk_output = ""
        # for i in range(len(puz)):
        #     if i % 9 == 0:
        #         sdk_output += "\n"
        #     if puz[i] == 0:
        #         sdk_output += "."
        #     else:
        #         sdk_output += str(puz[i])
        # print(sdk_output)
    pdf = FPDF()
    pdf.add_page()
    if len(puzzles) > 1:
        pdf.image("output/Easy.png", w=pdf.epw / 2, y=10, keep_aspect_ratio=True)
        pdf.image(
            "output/Medium.png", w=pdf.epw / 2, x=Align.R, y=60, keep_aspect_ratio=True
        )
        pdf.image(
            "output/Hard.png", w=pdf.epw / 2, x=Align.L, y=120, keep_aspect_ratio=True
        )
    pdf.output("output/sudoku.pdf")


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
    except Exception as e:
        print(f"Some sort of error: {e}", sys.stdout)
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
