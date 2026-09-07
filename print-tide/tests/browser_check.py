"""Playwright walkthrough of the demo UI.

NOT part of `python3 -m unittest discover -s tests`, and NOT executed by Claude:
this workspace has no browser and no Playwright, so treat this as a written
script for Codex to run, not as evidence.

    python3 -m light_studio --demo --port 8872 --data /tmp/print-tide-demo &
    python3 tests/browser_check.py

Writes screenshots to evidence/ and prints a JSON summary.
"""
import json
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
(ROOT / 'evidence').mkdir(exist_ok=True)
URL = 'http://127.0.0.1:8872'
passed = []


def step(name):
    passed.append(name)


with sync_playwright() as play:
    browser = play.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1500, 'height': 1150})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)

    page.goto(URL)
    page.wait_for_selector('.bay')
    page.wait_for_load_state('networkidle')

    assert page.locator('.bay').count() == 7, 'expected seven bays'
    expect(page.locator('#connection')).to_contain_text('DEMO')
    step('seven bays, demo labelled')

    nodes = page.locator('.bay').evaluate_all('els => els.map(e => e.dataset.node)')
    printers = page.locator('.bay').evaluate_all('els => els.map(e => e.dataset.printer)')

    # -- draft is visibly a draft, and only a save reaches the wall -----------
    page.locator('.bay select').first.select_option(printers[1])
    expect(page.locator('#draft-status')).to_contain_text('Draft')
    expect(page.locator('.bay').first).to_have_class(re.compile('drafted'))
    expect(page.locator('#save-note')).to_contain_text('printer assignment')
    step('draft badge and pending-change summary')

    page.locator('#save').click()
    expect(page.locator('#draft-status')).to_have_text('Saved layout')
    page.reload()
    page.wait_for_selector('.bay')
    expect(page.locator('.bay select').first).to_have_value(printers[1])
    step('save persists across reload')

    page.locator('#undo').click()
    expect(page.locator('.bay select').first).to_have_value(printers[0])
    step('undo restores the previous saved layout')

    # -- drag swap and discard ------------------------------------------------
    page.locator('.bay .printer-name').nth(0).drag_to(page.locator('.bay .printer-name').nth(2))
    expect(page.locator('.bay select').first).to_have_value(printers[2])
    page.locator('#cancel').click()
    expect(page.locator('.bay select').first).to_have_value(printers[0])
    step('drag swap and discard draft')

    # -- physical order -------------------------------------------------------
    page.get_by_role('button', name=f'Move {nodes[0]} one bay right', exact=True).click()
    assert page.locator('.bay').first.get_attribute('data-node') == nodes[1]
    page.locator('#cancel').click()
    step('bay reordering')

    # -- direction and rename -------------------------------------------------
    page.locator('.bay .reverse input').first.check()
    expect(page.locator('.bay .alias').first).to_contain_text('fills toward the wire')
    page.locator('#cancel').click()
    expect(page.locator('.bay .alias').first).to_contain_text('fills away from the wire')
    expect(page.locator('.bay .alias').first).to_contain_text('90 status + 10 cap')

    page.locator('.bay .printer-name').first.click()
    page.locator('.rename-input').fill('Corner rope')
    page.locator('.rename-input').press('Enter')
    expect(page.locator('.bay .printer-name').first).to_have_text('Corner rope')
    page.locator('#cancel').click()
    step('rope direction and inline rename')

    # -- identify is bounded and self-restoring -------------------------------
    page.locator('.bay .identify').first.click()
    expect(page.locator('.bay').first).to_have_class(re.compile('identifying'))
    expect(page.locator('.bay').first).not_to_have_class(re.compile('identifying'),
                                                         timeout=9000)
    step('identify highlights then restores itself')

    # -- guided walk ----------------------------------------------------------
    page.locator('#walk').click()
    expect(page.locator('#walk-title')).to_contain_text('Position 1 of 7')
    page.locator('#walk-next').click()          # "not this one, try another"
    page.locator('#walk-here').click()
    expect(page.locator('#walk-title')).to_contain_text('Position 2 of 7')
    page.locator('#walk-close').click()
    page.locator('#cancel').click()
    step('walk the wall flow')

    # -- the far-end accent cap ----------------------------------------------
    cap = page.locator('.bay .accent').first
    expect(cap.locator('summary')).to_contain_text('Far-end cap · white')
    cap.locator('summary').click()

    cap.locator('.accent-mode').select_option('color')
    expect(page.locator('#draft-status')).to_contain_text('Draft')
    expect(page.locator('#save-note')).to_contain_text('far-end cap')
    cap.locator('.accent-color').fill('#ff0000')
    page.wait_for_timeout(500)
    step('per-rope cap: colour picker marks the draft')

    # Only this rope changed; the rest are still white.
    expect(page.locator('.bay .accent summary').nth(1)).to_contain_text('white')
    cap.locator('.accent-all').click()
    expect(page.locator('.bay .accent summary').nth(1)).to_contain_text('colour')
    step('apply this cap to all ropes')

    page.locator('#cancel').click()
    expect(page.locator('.bay .accent summary').first).to_contain_text('white')
    step('discard restores the saved caps')

    page.screenshot(path=str(ROOT / 'evidence/desktop-map.png'), full_page=True)

    # -- live ropes actually animate -----------------------------------------
    first = page.locator('.bay canvas').first
    before = first.evaluate('e => e.toDataURL()')
    page.wait_for_timeout(900)
    assert before != first.evaluate('e => e.toDataURL()'), 'live bay is not animating'
    step('live bay animation moves')

    # -- animation lab, per bay ----------------------------------------------
    page.locator('#lab-tab').click()
    page.wait_for_timeout(800)
    lab = page.locator('#lab-canvas')
    a = lab.evaluate('e => e.toDataURL()')
    page.wait_for_timeout(1200)
    assert a != lab.evaluate('e => e.toDataURL()'), 'lab preview is not animating'
    step('lab preview animates')

    states = ['idle', 'preparing', 'printing', 'paused', 'error', 'finished',
              'offline', 'unknown']
    for index, state in enumerate(states[:7]):
        page.locator('.sim-cell select').nth(index).select_option(state)
    page.wait_for_timeout(900)
    mixed = lab.evaluate('e => e.toDataURL()')
    page.locator('#scenarios button').first.click()      # Quiet night: all idle
    page.wait_for_timeout(900)
    assert mixed != lab.evaluate('e => e.toDataURL()'), 'scenario did not change the wall'
    step('per-bay mixed states and scenarios')

    # -- wall-wide cap controls in the lab -----------------------------------
    page.locator('#cap-mode').select_option('rainbow')
    page.locator('#cap-apply').click()
    expect(page.locator('#save-note')).to_contain_text('far-end cap')
    page.wait_for_timeout(1100)
    rainbow_a = lab.evaluate('e => e.toDataURL()')
    page.wait_for_timeout(1400)
    assert rainbow_a != lab.evaluate('e => e.toDataURL()'), 'rainbow cap is static'
    page.locator('#save').click()
    expect(page.locator('#draft-status')).to_have_text('Saved layout')
    page.locator('#map-tab').click()
    expect(page.locator('.bay .accent summary').first).to_contain_text('rainbow')
    page.locator('#undo').click()
    expect(page.locator('.bay .accent summary').first).to_contain_text('white')
    page.locator('#lab-tab').click()
    step('wall-wide rainbow caps: apply, animate, save, undo')

    # -- settings belong to the same draft -----------------------------------
    page.locator('#brightness').fill('35')
    expect(page.locator('#draft-status')).to_contain_text('Draft')
    expect(page.locator('#save-note')).to_contain_text('wall settings')
    page.locator('#save').click()
    expect(page.locator('#draft-status')).to_have_text('Saved layout')
    page.reload()
    page.wait_for_selector('.bay')
    page.locator('#lab-tab').click()
    expect(page.locator('#brightness')).to_have_value('35')
    page.locator('#brightness').fill('65')
    page.locator('#save').click()
    page.wait_for_timeout(600)
    page.screenshot(path=str(ROOT / 'evidence/desktop-lab.png'), full_page=True)
    step('wall settings live in the draft and persist')

    # -- phone ---------------------------------------------------------------
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#map-tab').click()
    page.wait_for_timeout(400)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), \
        'horizontal overflow on a phone width'
    page.locator('.bay select').first.select_option(printers[1])
    expect(page.locator('#draft-status')).to_contain_text('Draft')
    page.locator('#cancel').click()
    page.screenshot(path=str(ROOT / 'evidence/mobile-map.png'), full_page=True)
    step('phone width: no overflow, swap without dragging')

    assert errors == [], errors
    step('no JavaScript errors')
    print(json.dumps({'passed': passed}, indent=2))
    browser.close()
