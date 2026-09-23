"""Plain-language explanations for common preflight findings.

Each category matches preflight messages (from PitStop, Acrobat, or any tool)
by keyword and carries three explanations:

* ``customer`` - no jargon, says why it matters and what to send back.
* ``csr``      - what it means for the order: can prepress fix it in-house,
                 does the customer need to act, is there a quality risk.
* ``prepress`` - the technical reading and the usual fix.

Tune the wording and ``fix_owner`` to match your shop's policies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class IssueCategory:
    key: str
    title: str
    patterns: tuple[str, ...]
    customer: str
    csr: str
    prepress: str
    # Who normally resolves it: "prepress" (we fix in-house), "customer"
    # (needs new artwork or a decision), or "either".
    fix_owner: str = "either"
    # Hint that a PitStop Action List / Switch flow can fix it without a human.
    auto_fixable: bool = False

    def matches(self, text: str) -> bool:
        return any(re.search(p, text, re.IGNORECASE) for p in self.patterns)


CATEGORIES: tuple[IssueCategory, ...] = (
    IssueCategory(
        key="low_resolution",
        title="Low-resolution images",
        patterns=(r"resolution", r"\b[pd]pi\b", r"pixels per inch", r"upsampl"),
        customer=(
            "One or more pictures in your file don't have enough detail for print. They look fine on "
            "screen, but printed they can come out blurry or blocky. Please send the original, "
            "full-size photos (not ones copied from a website or email), or approve printing as-is."
        ),
        csr=(
            "Image resolution is below the print minimum. Prepress can't add real detail, so the "
            "customer needs to supply better images or sign off on the quality risk. Mention which "
            "pages are affected."
        ),
        prepress=(
            "Effective image resolution below the profile threshold. Check the effective ppi at "
            "placed size. Get replacement art or a signed approval; don't upsample to hide it."
        ),
        fix_owner="customer",
    ),
    IssueCategory(
        key="rgb_color",
        title="RGB color (screen colors)",
        patterns=(r"\bRGB\b", r"DeviceRGB", r"color ?space.{0,60}(not|isn't).{0,60}CMYK", r"Lab color"),
        customer=(
            "Some colors in your file are set up for screens (RGB) rather than printing ink (CMYK). "
            "We convert them for you, but bright blues, greens and oranges may look a little duller "
            "on paper than on your monitor."
        ),
        csr=(
            "RGB content. Prepress converts it to CMYK routinely; only raise it with the customer if "
            "the job is color-critical (brand colors, vivid photos) and a proof is recommended."
        ),
        prepress=(
            "RGB/Lab objects present. Convert to the press output intent with the house ICC workflow "
            "(PitStop Action List or Switch color conversion). Watch for rich blacks from RGB 0/0/0."
        ),
        fix_owner="prepress",
        auto_fixable=True,
    ),
    IssueCategory(
        key="fonts_not_embedded",
        title="Missing fonts",
        patterns=(r"font.{0,60}not (embedded|included)", r"not embedded", r"missing font", r"font.{0,60}missing",
                  r"Type ?3 font", r"font.{0,60}subset"),
        customer=(
            "A font (typeface) used in your file wasn't included when the PDF was saved. That can "
            "make text print in the wrong typeface or with odd characters. Please re-export the PDF "
            "with fonts embedded, or send the original design file with its fonts."
        ),
        csr=(
            "Font not embedded. Prepress may be able to embed it if we own the font, otherwise we need "
            "a new PDF. Don't promise a date until prepress confirms."
        ),
        prepress=(
            "Unembedded font. Try embedding from the licensed font library; if unavailable request a "
            "new PDF with all fonts embedded (PDF/X export). Check for substituted glyphs."
        ),
        fix_owner="either",
    ),
    IssueCategory(
        key="missing_bleed",
        title="Missing or insufficient bleed",
        patterns=(r"bleed",),
        customer=(
            "Anything that should print right to the edge of the page needs to extend a little past "
            "the edge (usually 1/8\" or 3 mm) so there's no thin white line after trimming. Your file "
            "doesn't have that extra margin. We can sometimes extend it for you, or you can re-export "
            "with bleed turned on."
        ),
        csr=(
            "Not enough bleed. Prepress can often generate it (mirror or stretch edges) for simple "
            "backgrounds; photos or patterns at the edge usually need new art. Ask prepress before "
            "going back to the customer."
        ),
        prepress=(
            "Bleed box missing or smaller than required, or artwork doesn't reach the bleed box. Use "
            "PitStop's bleed generation (mirror/stretch) where it's safe; otherwise request art."
        ),
        fix_owner="either",
        auto_fixable=True,
    ),
    IssueCategory(
        key="safety_margin",
        title="Text or content too close to the edge",
        patterns=(r"safe(ty)? (zone|area|margin)", r"too close", r"near (the )?trim", r"distance to trim",
                  r"inside margin", r"live area"),
        customer=(
            "Some text or important content is very close to the edge of the page. The cutting "
            "machine can shift slightly, so anything within about 1/8\" (3 mm) of the edge could be "
            "trimmed off. Please move it inward."
        ),
        csr=(
            "Content inside the trim safety margin. Risk is partial trimming of text/logos. Usually "
            "needs a customer decision (move it, or accept the risk)."
        ),
        prepress="Live content inside the safety margin. Flag with a marked-up proof; don't move content without approval.",
        fix_owner="customer",
    ),
    IssueCategory(
        key="page_size",
        title="Page size doesn't match the order",
        patterns=(r"page ?size", r"trim ?box", r"media ?box", r"trim size", r"page box", r"dimension",
                  r"orientation", r"different page sizes"),
        customer=(
            "The page size in your file doesn't match the size you ordered. We need to either scale "
            "the artwork (which changes margins slightly) or get a file at the correct size. Please "
            "let us know which you prefer."
        ),
        csr=(
            "Trim size in the file differs from the order spec. Confirm the ordered size first; "
            "small proportional differences can be scaled by prepress with customer approval."
        ),
        prepress=(
            "TrimBox/MediaBox does not match the job ticket. Check if it's a box-definition issue "
            "(fix boxes) or real artwork size (scale with approval or request art)."
        ),
        fix_owner="either",
    ),
    IssueCategory(
        key="spot_colors",
        title="Spot (Pantone) colors",
        patterns=(r"spot colou?r", r"separation", r"pantone", r"\bPMS\b", r"DeviceN", r"named colou?r"),
        customer=(
            "Your file uses special premixed ink colors (like Pantone). If your order is for standard "
            "full-color printing, we'll convert these to the closest match, which can shift the color "
            "slightly. Let us know if an exact brand color is important."
        ),
        csr=(
            "Spot colors present. Check the order: if it's a 4-color job, prepress converts them (small "
            "color shift). If the customer wants true spot ink, that's a pricing/press change."
        ),
        prepress=(
            "Spot/DeviceN separations present. Map or convert to process per the job ticket; merge "
            "duplicate spot names (e.g. 'PANTONE 185 C' vs 'PANTONE 185 CV')."
        ),
        fix_owner="prepress",
        auto_fixable=True,
    ),
    IssueCategory(
        key="ink_coverage",
        title="Too much ink",
        patterns=(r"ink (coverage|limit)", r"total area coverage", r"\bTAC\b", r"total ink"),
        customer=(
            "Some dark areas use more ink than the paper can absorb, which can cause smudging or slow "
            "drying. We adjust this for you automatically; the dark areas may look very slightly "
            "different."
        ),
        csr="Total ink over the press limit. Prepress fixes this with a color conversion; no customer action needed.",
        prepress="Total area coverage above the limit. Re-separate / apply the TAC-limiting ICC or PitStop fix.",
        fix_owner="prepress",
        auto_fixable=True,
    ),
    IssueCategory(
        key="thin_lines",
        title="Very thin lines",
        patterns=(r"line ?width", r"hairline", r"thin line", r"stroke.{0,60}(width|thin)", r"line weight"),
        customer=(
            "Some lines in your artwork are so thin they may not show up or may print broken. We can "
            "thicken them slightly, or you can adjust them in your design."
        ),
        csr="Hairlines below the printable minimum. Prepress can usually thicken them automatically.",
        prepress="Stroke weights below the minimum (commonly 0.25 pt). Thicken with a PitStop fix, check reversed lines.",
        fix_owner="prepress",
        auto_fixable=True,
    ),
    IssueCategory(
        key="small_text",
        title="Very small text",
        patterns=(r"text size", r"font size", r"point size", r"small text", r"text.{0,60}smaller than"),
        customer=(
            "Some text is very small and may be hard to read or print unclearly, especially if it's "
            "light-colored or on a dark background. Consider making it larger."
        ),
        csr="Text below the minimum readable size. Customer decision; flag it but it doesn't usually stop the job.",
        prepress="Text below the size threshold. Check for small reversed or multi-color text (registration risk).",
        fix_owner="customer",
    ),
    IssueCategory(
        key="overprint",
        title="Overprint settings",
        patterns=(r"overprint", r"knock ?out"),
        customer=(
            "Some objects are set to print on top of other colors in a way that can make white or "
            "light elements disappear. We'll correct this before printing."
        ),
        csr="Overprint problem (e.g. white set to overprint). Prepress fixes it; no customer action.",
        prepress="Unexpected overprint (white/light objects, or overprint in RGB). Remove with PitStop fix; preview separations.",
        fix_owner="prepress",
        auto_fixable=True,
    ),
    IssueCategory(
        key="registration_black",
        title="Registration color or rich black text",
        patterns=(r"registration", r"rich black", r"4[- ]colou?r black", r"black.{0,60}(CMY|process)"),
        customer=(
            "Some items use a special 'all inks' black that's meant only for printer's marks, or "
            "small text uses four inks. That can make text look blurry. We'll correct it."
        ),
        csr="Registration/rich black on text or fine art. Prepress fixes it.",
        prepress="Registration color used in artwork, or small text in 4-color black. Convert to 100K.",
        fix_owner="prepress",
        auto_fixable=True,
    ),
    IssueCategory(
        key="transparency",
        title="Transparency effects",
        patterns=(r"transparen", r"blend mode", r"soft mask", r"drop shadow"),
        customer=(
            "Your file uses see-through effects like shadows or glows. These usually print fine, but "
            "occasionally they cause unexpected lines or color changes, so we'll check a proof."
        ),
        csr="Live transparency. Usually OK on our RIP; ask prepress if a proof is warranted.",
        prepress="Live transparency present. Fine for APPE workflows; flatten only if the output path requires it.",
        fix_owner="prepress",
    ),
    IssueCategory(
        key="output_intent",
        title="PDF standard / color profile",
        patterns=(r"PDF/X", r"output ?intent", r"\bICC\b", r"colou?r profile", r"PDF version", r"compliance"),
        customer=(
            "Your PDF wasn't saved with the print-ready settings we recommend. This is usually "
            "something we handle, but using a 'PDF/X' or 'Press Quality' export next time avoids "
            "surprises."
        ),
        csr="PDF isn't PDF/X or has no output intent. Typically handled by prepress; send the customer our export guide.",
        prepress="Missing/incorrect output intent or PDF/X non-compliance. Assign the house output intent or normalize.",
        fix_owner="prepress",
        auto_fixable=True,
    ),
    IssueCategory(
        key="security",
        title="Password-protected or locked PDF",
        patterns=(r"encrypt", r"password", r"security", r"protected"),
        customer=(
            "Your PDF is password-protected or locked, so we can't prepare it for print. Please send "
            "a version without security settings."
        ),
        csr="Secured PDF. Needs an unlocked file from the customer.",
        prepress="Encrypted PDF / permissions restrict editing or printing. Request an unsecured file.",
        fix_owner="customer",
    ),
    IssueCategory(
        key="page_count",
        title="Page count doesn't match",
        patterns=(r"page count", r"number of pages", r"pages? (is|are) (not )?(a )?multiple", r"blank page",
                  r"empty page"),
        customer=(
            "The number of pages in your file doesn't match your order (for example, a booklet needs "
            "pages in multiples of 4, or there's a blank page). Please confirm the correct page count."
        ),
        csr="Page count mismatch or blank pages. Confirm with the customer before prepress adds or removes pages.",
        prepress="Page count doesn't match the ticket / signature multiple, or blank pages found.",
        fix_owner="customer",
    ),
    IssueCategory(
        key="annotations",
        title="Comments, form fields or hidden layers",
        patterns=(r"annotation", r"form field", r"comment", r"layer", r"optional content", r"hidden",
                  r"not visible", r"non-printing"),
        customer=(
            "Your file contains comments, form fields or hidden layers. These may not print the way "
            "you expect (or may print when you didn't mean them to). Please check that what you see "
            "is what you want printed."
        ),
        csr="Annotations/layers/hidden content. Prepress flattens or removes them; confirm intent if unclear.",
        prepress="Annotations, form fields, or optional content present. Flatten or remove per ticket; check hidden objects.",
        fix_owner="either",
        auto_fixable=True,
    ),
    IssueCategory(
        key="image_compression",
        title="Image compression quality",
        patterns=(r"JPEG", r"compression", r"16[- ]bit", r"bits per (component|channel)", r"JPEG ?2000"),
        customer=(
            "Some images were heavily compressed when the PDF was saved, which can show as blotchy "
            "areas. If you have higher-quality originals, please use them."
        ),
        csr="Compression/bit-depth warning. Usually informational; prepress decides if it's visible.",
        prepress="Lossy compression artefacts or unusual bit depth. Inspect at 100%; normalize 16-bit images.",
        fix_owner="either",
    ),
)

GENERIC = IssueCategory(
    key="other",
    title="Other technical issue",
    patterns=(),
    customer="Our prepress team found a technical detail in your file that they'll review. We'll let you know if anything is needed from you.",
    csr="Uncategorized preflight finding. Ask prepress whether it needs customer action.",
    prepress="Uncategorized; see the original message.",
    fix_owner="either",
)


def categorize(text: str) -> IssueCategory:
    text = text[:500]  # untrusted report text: keep matching time bounded
    for category in CATEGORIES:
        if category.matches(text):
            return category
    return GENERIC


def get_category(key: str) -> IssueCategory:
    for category in CATEGORIES:
        if category.key == key:
            return category
    return GENERIC
