#!/usr/bin/env python3
"""Render the generated SVG architecture diagrams as directly viewable PNG files."""

from pathlib import Path

from playwright.sync_api import sync_playwright


OUTPUT_DIR = Path(__file__).parent
DIAGRAMS = (
    ("react_architecture.svg", "react_architecture.png", 1200, 620),
    ("plan_and_execute_architecture.svg", "plan_and_execute_architecture.png", 1200, 580),
    ("project_overview.svg", "project_overview.png", 1200, 570),
)


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(device_scale_factor=2)
        for source_name, output_name, width, height in DIAGRAMS:
            page.set_viewport_size({"width": width, "height": height})
            page.goto((OUTPUT_DIR / source_name).as_uri())
            page.screenshot(
                path=str(OUTPUT_DIR / output_name),
                clip={"x": 0, "y": 0, "width": width, "height": height},
                timeout=120_000,
            )
            print(f"Wrote {output_name}")
        browser.close()


if __name__ == "__main__":
    main()
