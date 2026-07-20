"""App CSS injection."""

import streamlit as st

from .config import APP_ACCENT, APP_BG, APP_BORDER, APP_GRID, APP_MUTED, APP_PANEL, APP_PANEL_SOFT, APP_TEXT


def _inject_app_styles():
    st.markdown(
        f"""
        <style>
            :root {{
                --app-bg: {APP_BG};
                --app-panel: {APP_PANEL};
                --app-panel-soft: {APP_PANEL_SOFT};
                --app-border: {APP_BORDER};
                --app-grid: {APP_GRID};
                --app-text: {APP_TEXT};
                --app-muted: {APP_MUTED};
                --app-accent: {APP_ACCENT};
            }}

            .stApp {{
                background:
                    radial-gradient(circle at top left, rgba(34, 49, 30, 0.22) 0%, rgba(11, 16, 11, 0) 28%),
                    linear-gradient(180deg, #0c130c 0%, #0b100b 100%);
                color: var(--app-text);
            }}

            [data-testid="stAppViewContainer"] {{
                background: transparent;
            }}

            [data-testid="stHeader"] {{
                background: rgba(11, 16, 11, 0.72);
                border-bottom: 1px solid rgba(39, 50, 38, 0.6);
            }}

            [data-testid="stSidebar"] {{
                background: linear-gradient(180deg, #101710 0%, #0d140d 100%);
                border-right: 1px solid rgba(39, 50, 38, 0.75);
            }}

            [data-testid="stSidebar"] * {{
                color: var(--app-text);
            }}

            .block-container {{
                padding-top: 2.2rem;
                padding-bottom: 2.5rem;
                max-width: 1820px;
                padding-left: 2rem;
                padding-right: 2rem;
            }}

            h1, h2, h3 {{
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                letter-spacing: 0;
                font-weight: 700;
                text-transform: uppercase;
            }}

            p, label, .stCaption, .stMarkdown, .stText {{
                color: var(--app-text);
            }}

            [data-testid="stMetric"] {{
                background: rgba(17, 24, 17, 0.92);
                border: 1px solid rgba(39, 50, 38, 0.85);
                border-radius: 4px;
                padding: 0.9rem 1rem;
                box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02);
                min-height: 76px;
                position: relative;
                overflow: hidden;
            }}

            [data-testid="stMetricLabel"] {{
                color: var(--app-muted);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                letter-spacing: 0.14em;
                text-transform: uppercase;
                font-size: 0.68rem;
            }}

            [data-testid="stMetricValue"] {{
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-weight: 600;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                padding-right: 3.9rem;
            }}

            [data-testid="stMetricDelta"] {{
                position: absolute;
                right: 0.85rem;
                bottom: 0.72rem;
                margin: 0;
                max-width: 3.6rem;
                overflow: hidden;
                white-space: nowrap;
            }}

            [data-testid="stRadio"] > div,
            [data-testid="stNumberInputContainer"],
            [data-testid="stSlider"] {{
                background: rgba(17, 24, 17, 0.88);
                border: 1px solid rgba(39, 50, 38, 0.75);
                border-radius: 4px;
                padding: 0.45rem 0.55rem;
            }}

            .stButton > button,
            [data-baseweb="select"] > div,
            [data-baseweb="input"] > div {{
                background: rgba(17, 24, 17, 0.9);
                border: 1px solid rgba(39, 50, 38, 0.85);
                color: var(--app-text);
                border-radius: 4px;
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
            }}

            .stButton > button:hover {{
                border-color: rgba(94, 111, 87, 0.9);
                color: var(--app-accent);
            }}

            [data-testid="stDataFrame"],
            [data-testid="stTable"] {{
                border: 1px solid rgba(39, 50, 38, 0.85);
                border-radius: 4px;
                overflow: hidden;
                background: rgba(17, 24, 17, 0.84);
            }}

            [data-testid="stExpander"] {{
                border: 1px solid rgba(39, 50, 38, 0.78);
                border-radius: 4px;
                background: rgba(17, 24, 17, 0.6);
            }}

            .term-guide-grid {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 0.85rem;
                margin-top: 0.8rem;
            }}

            @media (max-width: 1100px) {{
                .term-guide-grid {{
                    grid-template-columns: minmax(0, 1fr);
                }}
            }}

            .term-guide-card {{
                background: rgba(20, 29, 20, 0.9);
                border: 1px solid rgba(39, 50, 38, 0.85);
                border-radius: 10px;
                padding: 0.9rem 1rem;
                box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02);
            }}

            .term-guide-title {{
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 0.98rem;
                font-weight: 700;
                line-height: 1.2;
                margin-bottom: 0.45rem;
            }}

            .term-guide-copy {{
                color: var(--app-muted);
                font-size: 0.84rem;
                line-height: 1.45;
            }}

            .term-guide-group {{
                margin-top: 1rem;
            }}

            .term-guide-section {{
                margin-top: 1rem;
                border: 1px solid rgba(39, 50, 38, 0.85);
                border-radius: 10px;
                background: rgba(17, 24, 17, 0.65);
                overflow: hidden;
            }}

            .term-guide-summary {{
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 1.02rem;
                font-weight: 700;
                letter-spacing: 0.12em;
                text-transform: uppercase;
                padding: 0.9rem 1rem;
                cursor: pointer;
                list-style: none;
                background: rgba(20, 29, 20, 0.92);
                border-bottom: 1px solid rgba(39, 50, 38, 0.75);
            }}

            .term-guide-summary::-webkit-details-marker {{
                display: none;
            }}

            .term-guide-summary::before {{
                content: "▸";
                display: inline-block;
                margin-right: 0.55rem;
                transition: transform 0.18s ease;
            }}

            details[open] > .term-guide-summary::before {{
                transform: rotate(90deg);
            }}

            .term-guide-section-body {{
                padding: 0 1rem 1rem 1rem;
            }}

            .gex-level-title {{
                font-size: 1.32rem;
                font-weight: 700;
                color: var(--app-text);
                margin: 0 0 0.55rem 0;
            }}

            .gex-level-list {{
                display: flex;
                flex-direction: column;
                gap: 0.95rem;
                margin: 0;
            }}

            .gex-level-item {{
                padding: 0.76rem 0.85rem;
                border: 1px solid rgba(39, 50, 38, 0.95);
                border-radius: 6px;
                background: rgba(17, 24, 17, 0.9);
                line-height: 1.82;
                font-size: 1.09rem;
                color: var(--app-text);
            }}

            .gex-chip {{
                display: inline-block;
                margin: 0 0.42rem 0.34rem 0;
                padding: 0.16rem 0.38rem;
                border: 1px solid rgba(39, 50, 38, 0.95);
                border-radius: 6px;
                background: rgba(17, 24, 17, 0.9);
                font-size: 1.09rem;
                font-family: "IBM Plex Mono", "Consolas", monospace;
                color: #7ef0a0;
            }}

            .options-snapshot-large [data-testid="stDataFrame"] {{
                font-size: 1.12rem;
            }}

            .options-snapshot-large [data-testid="stDataFrame"] [role="columnheader"] {{
                font-size: 0.98rem;
            }}

            .options-snapshot-table {{
                width: 100%;
                border-collapse: collapse;
                border: 1px solid rgba(39, 50, 38, 0.85);
                border-radius: 4px;
                overflow: hidden;
                background: rgba(17, 24, 17, 0.84);
                font-family: "IBM Plex Mono", "Consolas", monospace;
                font-size: 1.12rem;
            }}

            .options-snapshot-table thead th {{
                text-align: left;
                padding: 0.8rem 0.95rem;
                font-size: 0.98rem;
                font-weight: 600;
                color: var(--app-muted);
                background: rgba(28, 34, 28, 0.96);
                border-bottom: 1px solid rgba(39, 50, 38, 0.8);
            }}

            .options-snapshot-table tbody td {{
                padding: 0.82rem 0.95rem;
                border-top: 1px solid rgba(39, 50, 38, 0.55);
                color: var(--app-text);
            }}

            .jarvis-fab {{
                position: fixed;
                right: 1.2rem;
                bottom: 1.2rem;
                z-index: 999;
                width: min(360px, calc(100vw - 2rem));
            }}

            .jarvis-fab > summary {{
                list-style: none;
                cursor: pointer;
                margin-left: auto;
                width: fit-content;
                max-width: 100%;
                background: rgba(17, 24, 17, 0.96);
                border: 1px solid rgba(39, 50, 38, 0.92);
                border-radius: 999px;
                padding: 0.78rem 1rem;
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 0.94rem;
                font-weight: 700;
                box-shadow: 0 10px 24px rgba(0, 0, 0, 0.28);
            }}

            .jarvis-fab > summary::-webkit-details-marker {{
                display: none;
            }}

            .jarvis-panel {{
                margin-top: 0.75rem;
                border: 1px solid rgba(39, 50, 38, 0.92);
                border-radius: 10px;
                background: rgba(12, 18, 12, 0.98);
                box-shadow: 0 12px 28px rgba(0, 0, 0, 0.34);
                overflow: hidden;
                max-height: min(68vh, 620px);
                display: flex;
                flex-direction: column;
            }}

            .jarvis-panel-head {{
                padding: 0.9rem 1rem 0.75rem 1rem;
                border-bottom: 1px solid rgba(39, 50, 38, 0.72);
                background: rgba(20, 29, 20, 0.94);
            }}

            .jarvis-panel-title {{
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 1rem;
                font-weight: 700;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                margin: 0 0 0.35rem 0;
            }}

            .jarvis-panel-copy {{
                color: var(--app-muted);
                font-size: 0.84rem;
                line-height: 1.45;
            }}

            .jarvis-faq {{
                padding: 0.85rem 1rem 1rem 1rem;
                overflow-y: auto;
                max-height: calc(min(68vh, 620px) - 96px);
                scrollbar-width: thin;
                scrollbar-color: rgba(94, 111, 87, 0.9) rgba(17, 24, 17, 0.65);
            }}

            .jarvis-faq::-webkit-scrollbar {{
                width: 10px;
            }}

            .jarvis-faq::-webkit-scrollbar-track {{
                background: rgba(17, 24, 17, 0.65);
                border-left: 1px solid rgba(39, 50, 38, 0.45);
            }}

            .jarvis-faq::-webkit-scrollbar-thumb {{
                background: rgba(94, 111, 87, 0.9);
                border-radius: 999px;
                border: 2px solid rgba(17, 24, 17, 0.65);
            }}

            .jarvis-faq-item {{
                border: 1px solid rgba(39, 50, 38, 0.82);
                border-radius: 8px;
                background: rgba(17, 24, 17, 0.88);
                overflow: hidden;
            }}

            .jarvis-faq-item > summary {{
                list-style: none;
                cursor: pointer;
                padding: 0.82rem 0.9rem;
                color: var(--app-text);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 0.88rem;
                font-weight: 700;
                line-height: 1.4;
                background: rgba(20, 29, 20, 0.96);
                border-bottom: 1px solid rgba(39, 50, 38, 0.72);
            }}

            .jarvis-faq-item > summary::-webkit-details-marker {{
                display: none;
            }}

            .jarvis-faq-item > summary::before {{
                content: "›";
                display: inline-block;
                margin-right: 0.45rem;
                transition: transform 0.18s ease;
            }}

            .jarvis-faq-item[open] > summary::before {{
                transform: rotate(90deg);
            }}

            .jarvis-answer {{
                padding: 0.9rem 0.95rem 0.95rem 0.95rem;
            }}

            .jarvis-answer ul {{
                margin: 0;
                padding-left: 1.05rem;
            }}

            .jarvis-answer li {{
                color: var(--app-text);
                font-size: 0.84rem;
                line-height: 1.52;
                margin: 0 0 0.55rem 0;
            }}

            .jarvis-answer h4 {{
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 0.86rem;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                margin: 0.95rem 0 0.42rem 0;
            }}

            .jarvis-answer h4:first-child {{
                margin-top: 0;
            }}

            .jarvis-answer p {{
                color: var(--app-text);
                font-size: 0.84rem;
                line-height: 1.5;
                margin: 0 0 0.6rem 0;
            }}

            .jarvis-answer .jarvis-note {{
                color: var(--app-muted);
                font-size: 0.78rem;
                line-height: 1.45;
                margin-top: 0.8rem;
            }}

            .jarvis-answer .jarvis-simple-summary {{
                color: var(--app-muted);
                font-size: 0.67rem;
                line-height: 1.38;
            }}

            .jarvis-answer strong {{
                color: var(--app-accent);
            }}

            .positioning-grid {{
                display: grid;
                grid-template-columns: repeat(5, minmax(0, 1fr));
                gap: 0.75rem;
                margin: 0.85rem 0 1.15rem 0;
            }}

            @media (max-width: 1320px) {{
                .positioning-grid {{
                    grid-template-columns: repeat(3, minmax(0, 1fr));
                }}
            }}

            @media (max-width: 900px) {{
                .positioning-grid {{
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }}
            }}

            @media (max-width: 600px) {{
                [data-testid="stMetricValue"] {{
                    white-space: normal;
                    overflow: visible;
                    overflow-wrap: anywhere;
                    text-overflow: clip;
                    font-size: 0.9rem;
                    line-height: 1.25;
                }}

                [data-testid="stMetricDelta"] {{
                    max-width: 4.5rem;
                    font-size: 0.65rem;
                }}

                [data-testid="stMainBlockContainer"] {{
                    padding-bottom: 6rem;
                }}
            }}

            .positioning-card {{
                border: 1px solid rgba(39, 50, 38, 0.88);
                border-radius: 8px;
                background: rgba(17, 24, 17, 0.92);
                padding: 0.82rem 0.9rem 0.86rem 0.9rem;
                min-height: 122px;
            }}

            .positioning-head {{
                display: flex;
                align-items: center;
                gap: 0.55rem;
                margin-bottom: 0.52rem;
            }}

            .positioning-dot {{
                width: 14px;
                height: 14px;
                border-radius: 999px;
                border: 1px solid rgba(228, 234, 223, 0.18);
                flex: 0 0 auto;
            }}

            .positioning-title {{
                color: var(--app-accent);
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 0.82rem;
                font-weight: 700;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                line-height: 1.3;
            }}

            .positioning-status {{
                font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
                font-size: 0.98rem;
                font-weight: 700;
                margin-bottom: 0.35rem;
            }}

            .positioning-copy {{
                color: var(--app-muted);
                font-size: 0.79rem;
                line-height: 1.46;
            }}

            .positioning-green {{
                color: #7ef0a0;
            }}

            .positioning-yellow {{
                color: #f4d35e;
            }}

            .positioning-red {{
                color: #ff6b6b;
            }}

            hr {{
                border-color: rgba(39, 50, 38, 0.75);
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )
