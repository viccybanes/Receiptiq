---
name: ReceiptIQ
description: A local receipt review workspace built around source evidence and verified records.
colors:
  green: "#27634c"
  green-deep: "#1b4c39"
  green-wash: "#eef5ef"
  ground: "#f5f6f3"
  paper: "#ffffff"
  ink: "#22342b"
  muted: "#637168"
  wash: "#f3f6f2"
  line: "#dfe5de"
  line-soft: "#edf0eb"
  line-strong: "#c7d0c7"
  image-stage: "#e9ede8"
  error-ink: "#8c3026"
  error-paper: "#fdf0ed"
  error-line: "#ebc5bd"
  warning-ink: "#72500f"
  warning-paper: "#fff8e8"
  warning-line: "#e5d3a7"
typography:
  display:
    fontFamily: '"Segoe UI", system-ui, sans-serif'
    fontSize: "clamp(24px, 2.2vw, 32px)"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-.8px"
  title:
    fontFamily: '"Segoe UI", system-ui, sans-serif'
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.5
  body:
    fontFamily: '"Segoe UI", system-ui, sans-serif'
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  field:
    fontFamily: '"Segoe UI", system-ui, sans-serif'
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: '"Segoe UI", system-ui, sans-serif'
    fontSize: "11px"
    fontWeight: 600
    lineHeight: 1.5
rounded:
  field: "5px"
  button: "6px"
  workspace: "8px"
  dialog: "10px"
spacing:
  control-gap: "7px"
  field-block: "8px"
  control-inline: "12px"
  panel-inline: "18px"
  form-inline: "22px"
components:
  button-primary:
    backgroundColor: "{colors.green}"
    textColor: "{colors.paper}"
    rounded: "{rounded.button}"
    padding: "7px 12px"
  button-primary-hover:
    backgroundColor: "{colors.green-deep}"
    textColor: "{colors.paper}"
  button-secondary:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.button}"
    padding: "7px 12px"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.button}"
    padding: "7px 12px"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.field}"
    rounded: "{rounded.field}"
    padding: "8px 10px"
  workspace:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.workspace}"
  export-dialog:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.dialog}"
    padding: "0 22px 22px"
  error-notice:
    backgroundColor: "{colors.error-paper}"
    textColor: "{colors.error-ink}"
    rounded: "{rounded.field}"
    padding: "12px 15px"
---

# Design System: ReceiptIQ

## Overview

**Creative North Star: "A document examination desk"**

ReceiptIQ places a compact batch register beside the receipt image and an editable ledger. Pale mineral surfaces, dark ink, fine rules, and restrained evergreen actions keep attention on the comparison between source evidence and entered values. The system sans typography is self-contained for the local Windows workflow.

The workspace is dense where users scan receipts and more spacious where they inspect images or correct fields. Verification, saving, and processing have explicit text states; color supports those states rather than carrying their meaning alone.

**Key Characteristics:**

- A connected workspace with ruled panels rather than a collection of floating cards.
- Evergreen actions and selection accents on quiet white and mineral surfaces.
- Compact text, tabular numbers, and labeled evidence and verification states.
- A responsive queue, image stage, and review form with consistent reading order.

## Colors

The palette combines evergreen action colors with warm mineral neutrals and distinct warning and error treatments. Frontmatter records the implemented values; the shared CSS primitives live in `frontend/src/tokens.css`.

### Primary

- **Evergreen** (`green`): primary upload, verification, and export actions, keyboard focus, input carets, and brand details.
- **Deep evergreen** (`green-deep`): primary hover, verified status, and local-storage labels.
- **Evergreen wash** (`green-wash`): selected receipt rows and the export icon background.

### Neutral

- **Mineral ground** (`ground`): the outer page, status filter, and CSV headings.
- **White paper** (`paper`): the connected workspace, controls, and modal.
- **Dark ink** (`ink`): headings, body text, and entered values.
- **Mineral ink** (`muted`): supporting text, filenames, placeholders, and save status.
- **Soft wash** (`wash`): secondary button and queue hover.
- **Fine, soft, and strong rules** (`line`, `line-soft`, `line-strong`): panel divisions, receipt and item separators, and field outlines respectively.
- **Image-stage mineral** (`image-stage`): a neutral surround that distinguishes the receipt sheet from the editable form.

Warning paper, ink, and rule colors identify missing setup, unfinished uploads, and saved-version conflicts. Error paper, ink, and rule colors identify failed actions. Receipt statuses also use local amber, red, and blue text treatments alongside written labels and icons.

**The Evidence Rule.** Keep the yellow OCR region highlight on the receipt image; it denotes source evidence, not a verified field or an application action.

## Typography

**Display and body font:** Segoe UI, followed by system-ui and sans-serif. No remote font is required.

The hierarchy is compact and practical. Page headings have restrained negative tracking; form labels, filenames, and supporting text remain small so the source image and editable values carry the workspace.

- **Display:** the page heading uses the frontmatter display role. At the phone breakpoint its size becomes 26px.
- **Title:** panel headings use the title role. Line-item section headings are 13px and semibold.
- **Body:** the page uses the body role; instructions inside the review form use 12px and the introduction is limited to 60ch.
- **Field:** inputs use the field role and tabular numbers. CSV cells also use tabular numbers at 11px.
- **Label:** field labels use the label role. Queue names are 12px semibold; filenames are 11px; queue statuses are 10px with regular weight.

## Layout

The page is centered within a maximum width of 1760px. The desktop header is 74px tall; main content has 30px top padding and 36px horizontal padding. The "Add expense" heading and "Record a receipt with one or more line items. Each line gets its own item number on save." subtitle precede import and upload actions, batch controls, the connected review workspace, and export footer.

The desktop workbench uses a 244px queue followed by `minmax(280px, 1fr)` image and `minmax(350px, 1.04fr)` form columns. Its minimum height is 620px. At 1500px and wider, the queue becomes 260px and the image and form receive larger minimums. At 1150px and below, page padding and column minimums tighten.

At 920px and below, the queue moves above the two review columns. Receipt rows become a horizontally scrolling strip with a Show/Hide control; the queue footer disappears. At 600px and below, queue, image, and form stack vertically, the import and upload actions each fill the available width, secondary header text disappears, and the export footer stacks. The receipt field grid retains two columns: Purchase date beside Vendor, Receipt number beside Receipt photo, and full-width Notes. Each line card has a full-width Description above Quantity, Unit, Unit price, Amount, and Type. These five fields use five columns on desktop, three at 1150px and below, and two on phones; the optional item photo spans the card width.

Desktop form padding is 20px by 22px. Receipt field gaps are 15px vertically and 14px horizontally. Fine separators define item groups, verification, and history. The image stage scrolls independently for zoomed receipts. The CSV preview scrolls in its own container and keeps column headings visible.

## Elevation & Depth

The workspace is mostly flat: white panels, tonal differences, and thin rules establish structure. The receipt sheet lifts slightly from the image stage. The native export dialog has a stronger shadow and a tinted backdrop to establish modal focus. The selected queue row uses an inset outline rather than elevation.

- **Receipt sheet:** `0 5px 20px #1e352719` separates paper from the image stage.
- **Export dialog:** `0 12px 80px #192e2555` separates preview from the workspace.
- **Selected row:** `inset 0 0 0 1px #bed4c6` reinforces the selection wash.
- **Modal backdrop:** `#122c2466` dims the workspace beneath the native dialog.

## Shapes

Corners are gently curved: fields and notices use the field radius, buttons use the button radius, the connected workspace ends use the workspace radius, and the export dialog uses the dialog radius. Interior panels share straight dividing rules. Small circular status dots and checkboxes remain subordinate to the receipt name and written status.

## Components

### Buttons

Primary buttons are solid evergreen with white text; secondary buttons are white with a fine rule; quiet buttons have transparent backgrounds and borders. All use semibold text, a 7px icon gap, and a default minimum height of 36px. The leading upload action has 11px by 18px padding. Icon-only viewer controls are 32px square and have accessible names.

Primary hover darkens to deep evergreen. Other buttons acquire a wash and stronger border on hover. Disabled buttons retain their shape and use 0.48 opacity with a not-allowed cursor. Interactive controls receive a 2px evergreen focus outline offset by 3px.

### Workspace and containers

The batch bar, workbench, and export footer form one connected white container with fine borders. Panel titles align labels, icons, and status controls in a minimum 58px row. Validation and conflict panels use warm tinted surfaces within the form, with written guidance and actions.

### Inputs and fields

Inputs use white paper, a strong rule, the field radius, a 37px minimum height, and 8px by 10px padding. Focus changes the border to evergreen. Placeholders use mineral ink at full opacity. Receipt number explains that a missing vendor reference can stay blank; Notes and Unit have short contextual hints. Labels stay visible above values, and Purchase date uses the native date control. Blocking issues use a warm border, a near-white warm fill, and an accompanying error message; warning acknowledgments use checkboxes and a reason field.

### Line cards and photos

Line items sit in lightly rounded, outlined fieldsets with a muted legend showing the line position and stable Item number after saving. Description and the optional Photo of item purchased span the full width; compact value fields use 32px minimum heights. Total stays beside the Line items heading. A possible implicit discount appears in an evergreen-wash notice with an explicit confirmation action before Amount and Unit price are adjusted.

Receipt photo and item-photo controls accept user-uploaded JPEG or PNG images. A dashed Add photo or Replace photo control sits beside an original-attachment label or a 48px square thumbnail, with a quiet Remove photo action for uploaded images. Keyboard focus outlines the upload control. Images are evidence supplied by users, not decorative assets.

### Queue navigation and status

Receipt rows combine a number or export checkbox, supplier, filename, written status, and chevron. The active row has an evergreen wash and inset outline; hover has a soft wash. Names and filenames truncate within their row. Processing uses a small spinner, verified uses a check, and other states use a status dot beside text. These are inline labels, not filled pill chips.

### Receipt evidence viewer

The image stage holds a white receipt sheet and its subtle shadow. A compact toolbar selects source or enhanced image, changes zoom, and rotates for another OCR pass; page controls sit below the image. Focusing an extracted field can highlight matching OCR regions with translucent yellow rectangles. Recognized text is a disclosure containing selectable regions and explicitly labeled OCR scores.

### Verification and saving

Save state appears beside the receipt-details heading with a small icon and live text. Verification has its own reviewer field, instruction, primary action, and next-receipt action. The verified state explicitly says "Verified by a person" and explains that editing requires verification again. History is a separate disclosure below the form.

### Import, CSV preview, and errors

An outlined Import CSV + photos action sits beside the primary Upload receipts action. Short helper text explains selecting one CSV with referenced photos or one ZIP containing both. The preview offers Download CSV and Download CSV + photos actions; the latter packages the images in a ZIP.

The native modal is limited to 1000px or the viewport minus 32px, with an 85vh maximum height. A scrollable table has sticky headings, compact tabular text, and a note that receipt totals repeat per item row. Download failures appear as an alert **inside the open dialog**, directly below its heading, keeping the message visible with the retry action. The preview stays open after a failed download.

### Motion

Motion is limited to a 1.5-second linear spinner for active work. Reduced-motion preferences disable its animation. Hover and focus changes are immediate; there are no decorative entrance animations.

## Do's and Don'ts

### Do:

- **Do** keep receipt evidence adjacent to editable values on desktop and before them on phones.
- **Do** pair status colors with written labels and icons or dots.
- **Do** use the shared mineral ink at full opacity for input and textarea placeholders.
- **Do** display download errors inside the CSV dialog while keeping its retry action available.
- **Do** preserve tabular numbers, persistent field labels, and visible keyboard focus.
- **Do** honor reduced-motion preferences for working indicators.

### Don't:

- **Don't** use the OCR highlight as a verification indicator.
- **Don't** replace the connected workspace panels with independently elevated cards.
- **Don't** show a failed download only behind the modal backdrop.
- **Don't** communicate unread, reading, failed, or verified state through color alone.
