---
name: make_presentation
description: Create, render, inspect, and repair organic-luxury pitch deck presentations in python-pptx with widescreen 16:9 layouts and visual container card design system.
triggers:
  - presentation
  - pitch deck
  - powerpoint
  - pptx
  - slides deck
---

# Presentation Making Skill (make_presentation)

This skill provides a end-to-end framework and python script for creating premium, high-impact PowerPoint pitch decks with custom design systems, grid layouts, card containers, and typography.

## Design System & Rules

1. **Widescreen Canvas**: Always use 16:9 format (`13.333" x 7.5"`).
2. **Organic Luxury Palette**:
   - Canvas / Background: `#FCF9F2` (Warm Cream)
   - Accent Dark: `#1C3328` (Forest Green)
   - Primary Accent: `#D9822B` (Saffron)
   - Secondary Accent: `#9C5233` (Terracotta)
   - Card Background: `#FFFFFF` (Pure White) with `#E6E0D6` subtle border.
3. **Typography Standard**:
   - Display / Headers: Serif font (e.g. `Georgia`), bold, dark tone.
   - Body & Metrics: Sans-serif font (e.g. `Arial`), clear hierarchy, high-contrast text.
4. **Layout Architecture**:
   - Cover Slide: Full dark-green hero layout with bold title, subtitle, and kicker tag.
   - Narrative & Problem/Solution: 2-column or 3-column container cards with distinct headings, bullet icons, and visual grounding.
   - Market Opportunity: Stat cards with large hero numbers and text containers.
   - Business Model & Traction: 3-step value chain cards (Direct E-Commerce, Retail Flagship, High-Margin Spa) plus unit economics card.
   - Metrics & Investment Ask: 4-column metric grid featuring target metrics, revenue forecasts, LTV, and seed capital allocation.

## Steps Taken Throughout the Session

1. **Requirements & Aesthetic Definition**: Established the organic luxury beauty brand identity ("Bare & Fair Atelier") with warm cream backgrounds, rich forest green hero accents, and saffron/terracotta highlights.
2. **Layout & Grid Planning**: Structured a 5-slide core pitch deck narrative (Title/Hero, Problem & Solution, Market Opportunity, Business Model, Financial Targets & Ask).
3. **Card Container Architecture**: Designed custom helper functions in `python-pptx` to programmatically render rounded rectangle cards, padded text frames, category kickers, and footers with slide pagination.
4. **Native PowerPoint Generation**: Built `build_native_deck.py` using pure `python-pptx` to generate cleanly aligned vector shapes, text elements, and metrics directly in `.pptx` format.
5. **Quality Verification**: Verified widescreen bounds, typography spacing, color contrast, margin padding, and deck readability.

## Python Builder Script (`build_native_deck.py`)

```python
import sys
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

def create_deck():
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank_layout = prs.slide_layouts[6]

    # Color Palette (Bare & Fair Organic Luxury)
    BG_CREAM = RGBColor(252, 249, 242)       # #FCF9F2
    DARK_GREEN = RGBColor(28, 51, 40)         # #1C3328
    SAFFRON = RGBColor(217, 130, 43)         # #D9822B
    TERRACOTTA = RGBColor(156, 82, 51)       # #9C5233
    CARD_BG = RGBColor(255, 255, 255)        # Pure White
    CARD_BORDER = RGBColor(230, 224, 214)    # Subtle Cream Border
    TEXT_DARK = RGBColor(26, 36, 31)         # Charcoal
    TEXT_MUTED = RGBColor(100, 115, 105)     # Soft Slate Gray
    ACCENT_GOLD = RGBColor(197, 160, 89)     # Warm Gold

    FONT_HEAD = "Georgia"
    FONT_BODY = "Arial"

    TOTAL_SLIDES = 5

    def set_slide_bg(slide, color):
        bg = slide.background
        fill = bg.fill
        fill.solid()
        fill.fore_color.rgb = color

    def add_card(slide, left, top, width, height, bg_color=CARD_BG, border_color=CARD_BORDER):
        shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
        shape.fill.solid()
        shape.fill.fore_color.rgb = bg_color
        if border_color:
            shape.line.color.rgb = border_color
            shape.line.width = Pt(1)
        else:
            shape.line.fill.background()
        return shape

    def add_header(slide, category_tag, title):
        # Kicker Tag
        tb_tag = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.733), Inches(0.3))
        tf_tag = tb_tag.text_frame
        tf_tag.word_wrap = True
        tf_tag.margin_left = tf_tag.margin_top = tf_tag.margin_right = tf_tag.margin_bottom = 0
        p_tag = tf_tag.paragraphs[0]
        p_tag.text = category_tag.upper()
        p_tag.font.name = FONT_BODY
        p_tag.font.size = Pt(10)
        p_tag.font.bold = True
        p_tag.font.color.rgb = TERRACOTTA

        # Main Title
        tb_title = slide.shapes.add_textbox(Inches(0.8), Inches(0.7), Inches(11.733), Inches(0.6))
        tf_title = tb_title.text_frame
        tf_title.word_wrap = True
        tf_title.margin_left = tf_title.margin_top = tf_title.margin_right = tf_title.margin_bottom = 0
        p_title = tf_title.paragraphs[0]
        p_title.text = title
        p_title.font.name = FONT_HEAD
        p_title.font.size = Pt(22)
        p_title.font.bold = True
        p_title.font.color.rgb = DARK_GREEN

    def add_footer(slide, current_slide, total_slides=TOTAL_SLIDES):
        tb = slide.shapes.add_textbox(Inches(0.8), Inches(7.0), Inches(11.733), Inches(0.3))
        tf = tb.text_frame
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0
        p = tf.paragraphs[0]
        p.text = f"BARE & FAIR ATELIER  |  INVESTOR PITCH DECK  |  SLIDE 0{current_slide} OF 0{total_slides}"
        p.font.name = FONT_BODY
        p.font.size = Pt(9)
        p.font.color.rgb = TEXT_MUTED

    # ==========================================
    # SLIDE 1: COVER (Dark Luxury)
    # ==========================================
    slide1 = prs.slides.add_slide(blank_layout)
    set_slide_bg(slide1, DARK_GREEN)

    tb1 = slide1.shapes.add_textbox(Inches(1.0), Inches(2.0), Inches(11.333), Inches(3.8))
    tf1 = tb1.text_frame
    tf1.word_wrap = True

    p0 = tf1.paragraphs[0]
    p0.text = "INVESTOR PRESENTATION  •  SEED ROUND"
    p0.font.name = FONT_BODY
    p0.font.size = Pt(11)
    p0.font.bold = True
    p0.font.color.rgb = SAFFRON
    p0.space_after = Pt(20)

    p1 = tf1.add_paragraph()
    p1.text = "BARE & FAIR ATELIER"
    p1.font.name = FONT_HEAD
    p1.font.size = Pt(48)
    p1.font.bold = True
    p1.font.color.rgb = BG_CREAM
    p1.space_after = Pt(16)

    p2 = tf1.add_paragraph()
    p2.text = "Redefining luxury clean beauty with authentic heritage formulations, unyielding ingredient transparency, and high-margin direct-to-consumer rituals."
    p2.font.name = FONT_BODY
    p2.font.size = Pt(16)
    p2.font.color.rgb = ACCENT_GOLD
    p2.space_after = Pt(28)

    add_footer(slide1, 1)

    # ==========================================
    # SLIDE 2: THE MARKET OPPORTUNITY & PROBLEM
    # ==========================================
    slide2 = prs.slides.add_slide(blank_layout)
    set_slide_bg(slide2, BG_CREAM)
    add_header(slide2, "Market Challenge & Opportunity", "Synthesizing Clean Beauty Integrity with Uncompromising Luxury")
    add_footer(slide2, 2)

    # Card 1: Problem
    add_card(slide2, Inches(0.8), Inches(1.6), Inches(5.6), Inches(5.0))
    tb_p = slide2.shapes.add_textbox(Inches(1.1), Inches(1.9), Inches(5.0), Inches(4.4))
    tf_p = tb_p.text_frame
    tf_p.word_wrap = True

    p_p0 = tf_p.paragraphs[0]
    p_p0.text = "THE MARKET VOID"
    p_p0.font.name = FONT_BODY
    p_p0.font.size = Pt(11)
    p_p0.font.bold = True
    p_p0.font.color.rgb = TERRACOTTA
    p_p0.space_after = Pt(12)

    p_p1 = tf_p.add_paragraph()
    p_p1.text = "Modern consumers are forced to choose between clinical purity and sensory luxury."
    p_p1.font.name = FONT_HEAD
    p_p1.font.size = Pt(18)
    p_p1.font.bold = True
    p_p1.font.color.rgb = DARK_GREEN
    p_p1.space_after = Pt(14)

    problems = [
        "Conventional luxury brands rely on synthetic fillers and artificial fragrances.",
        "Clean formulations often sacrifice tactile indulgence, packaging aesthetic, and shelf presence.",
        "Lack of provenance transparency erodes trust among discerning premium beauty buyers."
    ]
    for p in problems:
        pt = tf_p.add_paragraph()
        pt.text = f"•  {p}"
        pt.font.name = FONT_BODY
        pt.font.size = Pt(12)
        pt.font.color.rgb = TEXT_DARK
        pt.space_after = Pt(8)

    # Card 2: Solution
    add_card(slide2, Inches(6.9), Inches(1.6), Inches(5.6), Inches(5.0))
    tb_s = slide2.shapes.add_textbox(Inches(7.2), Inches(1.9), Inches(5.0), Inches(4.4))
    tf_s = tb_s.text_frame
    tf_s.word_wrap = True

    p_s0 = tf_s.paragraphs[0]
    p_s0.text = "THE BARE & FAIR PROMISE"
    p_s0.font.name = FONT_BODY
    p_s0.font.size = Pt(11)
    p_s0.font.bold = True
    p_s0.font.color.rgb = SAFFRON
    p_s0.space_after = Pt(12)

    p_s1 = tf_s.add_paragraph()
    p_s1.text = "Bio-active, ethically sourced formulations encased in artisan luxury vessels."
    p_s1.font.name = FONT_HEAD
    p_s1.font.size = Pt(18)
    p_s1.font.bold = True
    p_s1.font.color.rgb = DARK_GREEN
    p_s1.space_after = Pt(14)

    solutions = [
        "100% Traceable Ingredients: Cold-pressed botanical actives sourced directly from heritage ethical farms.",
        "Sensory Ritual Design: Formulations engineered for exquisite texture, absorption, and natural scent profiles.",
        "Sustainable Luxury Packaging: Refillable glass and ceramic vessels designed for longevity."
    ]
    for s in solutions:
        pt = tf_s.add_paragraph()
        pt.text = f"✓  {s}"
        pt.font.name = FONT_BODY
        pt.font.size = Pt(12)
        pt.font.color.rgb = TEXT_DARK
        pt.space_after = Pt(8)

    # ==========================================
    # SLIDE 3: MARKET & TRACTION CARDS
    # ==========================================
    slide3 = prs.slides.add_slide(blank_layout)
    set_slide_bg(slide3, BG_CREAM)
    add_header(slide3, "Market Dynamics & Growth Drivers", "Capturing High-Margin Share in the Fast-Growing Conscious Luxury Segment")
    add_footer(slide3, 3)

    pillars = [
        ("TAM $180B+", "Global Premium Beauty", "Expanding rapidly as high-net-worth consumers shift toward clean, conscious prestige skincare."),
        ("SAM $34B", "Clean Skincare Segment", "Growing at 12.4% CAGR, driven by ingredient awareness and premium wellness rituals."),
        ("SOM $120M", "Target Core Market", "Focused on affluent millennials and Gen-X consumers seeking transparent luxury.")
    ]

    for idx, (stat, title, desc) in enumerate(pillars):
        left_pos = Inches(0.8 + idx * 3.98)
        add_card(slide3, left_pos, Inches(1.6), Inches(3.7), Inches(5.0))
        
        tb = slide3.shapes.add_textbox(left_pos + Inches(0.25), Inches(1.9), Inches(3.2), Inches(4.4))
        tf = tb.text_frame
        tf.word_wrap = True

        p_st = tf.paragraphs[0]
        p_st.text = stat
        p_st.font.name = FONT_HEAD
        p_st.font.size = Pt(32)
        p_st.font.bold = True
        p_st.font.color.rgb = TERRACOTTA
        p_st.space_after = Pt(8)

        p_ti = tf.add_paragraph()
        p_ti.text = title
        p_ti.font.name = FONT_HEAD
        p_ti.font.size = Pt(16)
        p_ti.font.bold = True
        p_ti.font.color.rgb = DARK_GREEN
        p_ti.space_after = Pt(12)

        p_de = tf.add_paragraph()
        p_de.text = desc
        p_de.font.name = FONT_BODY
        p_de.font.size = Pt(12)
        p_de.font.color.rgb = TEXT_DARK

    # ==========================================
    # SLIDE 4: BUSINESS MODEL & MONETIZATION
    # ==========================================
    slide4 = prs.slides.add_slide(blank_layout)
    set_slide_bg(slide4, BG_CREAM)
    add_header(slide4, "Go-To-Market & Revenue Engine", "Multi-Channel Growth Strategy Driven by High Gross Margins and Repeat Subscriptions")
    add_footer(slide4, 4)

    # 3 Strategy Cards
    strats = [
        ("01 / Direct-to-Consumer", "Digital Atelier & Ritual Subscriptions", "Core e-commerce platform offering customized ritual bundles and automatic replenishment cycles with 78% gross margins."),
        ("02 / Selective Prestige Retail", "Flagship Boutiques & High-End Retail", "Targeted partnerships with luxury department stores and boutique apothecaries to enhance physical presence and brand authority."),
        ("03 / Hospitality & Spa Partnerships", "Exclusive Hotel & Spa Amenities", "B2B partnerships with world-class wellness retreats and boutique luxury hotels driving high-volume trial and customer acquisition.")
    ]

    for idx, (num, title, desc) in enumerate(strats):
        left_pos = Inches(0.8 + idx * 3.98)
        add_card(slide4, left_pos, Inches(1.6), Inches(3.7), Inches(5.0))
        
        tb = slide4.shapes.add_textbox(left_pos + Inches(0.25), Inches(1.9), Inches(3.2), Inches(4.4))
        tf = tb.text_frame
        tf.word_wrap = True

        p_num = tf.paragraphs[0]
        p_num.text = num
        p_num.font.name = FONT_BODY
        p_num.font.size = Pt(11)
        p_num.font.bold = True
        p_num.font.color.rgb = SAFFRON
        p_num.space_after = Pt(10)

        p_ti = tf.add_paragraph()
        p_ti.text = title
        p_ti.font.name = FONT_HEAD
        p_ti.font.size = Pt(17)
        p_ti.font.bold = True
        p_ti.font.color.rgb = DARK_GREEN
        p_ti.space_after = Pt(12)

        p_de = tf.add_paragraph()
        p_de.text = desc
        p_de.font.name = FONT_BODY
        p_de.font.size = Pt(12)
        p_de.font.color.rgb = TEXT_DARK

    # ==========================================
    # SLIDE 5: UNIT ECONOMICS & SEED ASK
    # ==========================================
    slide5 = prs.slides.add_slide(blank_layout)
    set_slide_bg(slide5, BG_CREAM)
    add_header(slide5, "Financial Projections & Capital Ask", "Disciplined Growth Strategy Anchored in Strong Unit Economics and Scalable Margins")
    add_footer(slide5, 5)

    metrics = [
        ("82%", "Target Gross Margin", "Unlocking strong profitability across signature skincare serums and botanical oil formulations."),
        ("$210", "Average Order Value", "Exceptional customer lifetime value driven by 60-day replacement cycles and ritual bundles."),
        ("$4.2M", "Year 3 Revenue Target", "Scale via direct e-commerce growth, selective retail, and flagship spa partnerships."),
        ("$1.5M", "Seed Round Capital Ask", "Allocated to inventory & supply chain (40%), brand marketing (35%), and key executive hires (25%).")
    ]

    for idx, (mval, mlbl, mdesc) in enumerate(metrics):
        left_pos = Inches(0.8 + idx * 2.98)
        is_dark = (idx == 3)
        bg = DARK_GREEN if is_dark else CARD_BG
        border = None if is_dark else CARD_BORDER
        
        add_card(slide5, left_pos, Inches(1.6), Inches(2.8), Inches(5.0), bg_color=bg, border_color=border)
        
        tb_m = slide5.shapes.add_textbox(left_pos + Inches(0.25), Inches(1.9), Inches(2.3), Inches(4.4))
        tf_m = tb_m.text_frame
        tf_m.word_wrap = True

        p_mv = tf_m.paragraphs[0]
        p_mv.text = mval
        p_mv.font.name = FONT_HEAD
        p_mv.font.size = Pt(36)
        p_mv.font.bold = True
        p_mv.font.color.rgb = SAFFRON if is_dark else TERRACOTTA
        p_mv.space_after = Pt(8)

        p_ml = tf_m.add_paragraph()
        p_ml.text = mlbl
        p_ml.font.name = FONT_HEAD
        p_ml.font.size = Pt(15)
        p_ml.font.bold = True
        p_ml.font.color.rgb = BG_CREAM if is_dark else DARK_GREEN
        p_ml.space_after = Pt(12)

        p_md = tf_m.add_paragraph()
        p_md.text = mdesc
        p_md.font.name = FONT_BODY
        p_md.font.size = Pt(11)
        p_md.font.color.rgb = BG_CREAM if is_dark else TEXT_MUTED

    prs.save("bare_fair_investor_pitch.pptx")
    print("Successfully built bare_fair_investor_pitch.pptx with 5 comprehensive, highly refined slides!")

if __name__ == "__main__":
    create_deck()
```
