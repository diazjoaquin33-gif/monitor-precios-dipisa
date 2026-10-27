"""Página «Convenio Marco Resmas» del panel de Convenio Marco (la lógica está en panel_convenio.py)."""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

st.set_page_config(page_title="Convenio Marco Resmas | Dipisa", page_icon="📄", layout="wide")

from panel_convenio import mostrar  # noqa: E402

mostrar("resmas")
