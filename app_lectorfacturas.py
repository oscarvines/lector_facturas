import io
import os
import shutil
import streamlit as st
import pandas as pd
import json
import re
from google.oauth2 import service_account
from google.cloud import documentai_v1 as documentai
from PyPDF2 import PdfReader, PdfWriter  # Necesario para la V3
from procesador_facturas import init_docai_client, procesar_archivo

# --- CONFIGURACIÓN ---
PROJECT_ID   = "772723410003"
LOCATION     = "us"
PROCESSOR_ID = "dff8117c158462cd" # Usando tu nuevo procesador de la V3

# --- Autenticación con st.secrets ---
info = json.loads(st.secrets["google"]["credentials"])
creds = service_account.Credentials.from_service_account_info(info)
docai_client = init_docai_client(creds)
PROCESSOR_VERSION_ID = "e4fb17a2603c6087"
processor_name = f"projects/{PROJECT_ID}/locations/{LOCATION}/processors/{PROCESSOR_ID}/processorVersions/{PROCESSOR_VERSION_ID}"

# --- STREAMLIT UI (Se mantiene similar pero con la nueva lógica) ---

st.set_page_config(page_title="Lector Facturas V3", layout="wide")
st.title("📄 Lector de Facturas Pro (V3 - Ahorro de Costes)")

if "uploaded_files_data" not in st.session_state:
    st.session_state.uploaded_files_data = {} # {filename: bytes}

uploaded_files = st.file_uploader("Sube tus PDFs", type="pdf", accept_multiple_files=True)

if uploaded_files:
    for f in uploaded_files:
        if f.name not in st.session_state.uploaded_files_data:
            st.session_state.uploaded_files_data[f.name] = f.read()
    st.info(f"Archivos listos: {len(st.session_state.uploaded_files_data)}")

if st.button("🚀 Procesar con Lógica V3"):
    todos_los_resultados = []
    total_archivos = len(st.session_state.uploaded_files_data)
    progreso = st.progress(0)
    
    with st.spinner("Analizando y filtrando justificantes..."):
        for i, (name, b) in enumerate(st.session_state.uploaded_files_data.items()):
            facturas, lineas = procesar_archivo(b, name, docai_client, processor_name)
            todos_los_resultados.extend(facturas)

            if "lineas" not in st.session_state:
                st.session_state.lineas = []

            st.session_state.lineas.extend(lineas)
            progreso.progress((i + 1) / total_archivos)
            
    if todos_los_resultados:
        df_facturas = pd.DataFrame(todos_los_resultados)
        df_lineas = pd.DataFrame(st.session_state.lineas)

        st.session_state.resultados = df_facturas
        st.session_state.lineas_df = df_lineas
        st.success(f"Proceso finalizado. Se extrajeron {len(df_facturas)} filas útiles.")
    else:
        st.warning("No se encontraron facturas válidas (¿eran todos justificantes?)")

if "resultados" in st.session_state and st.session_state.resultados is not None:
    st.subheader("📄 Facturas")
    st.dataframe(st.session_state.resultados)

    st.subheader("📦 Líneas (editable)")
    st.session_state.lineas_df = st.data_editor(st.session_state.lineas_df)

    # Recalcular total aceptado
    df_lineas_filtrado = st.session_state.lineas_df[st.session_state.lineas_df["aceptada"] == True]
    totales = df_lineas_filtrado.groupby("id_factura")["importe"].sum()

    st.session_state.resultados["total_aceptado"] = st.session_state.resultados["id_factura"].map(totales).fillna(0)
    
    # Descarga Excel
    towrite = io.BytesIO()
    with pd.ExcelWriter(towrite, engine="openpyxl") as writer:
        st.session_state.resultados.to_excel(writer, sheet_name="Facturas", index=False)
        st.session_state.lineas_df.to_excel(writer, sheet_name="Lineas", index=False)
    st.download_button(
        label="⬇️ Descargar Excel V3",
        data=towrite.getvalue(),
        file_name="facturas_v3.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

if st.button("🗑️ Limpiar Todo"):
    st.session_state.uploaded_files_data = {}
    st.session_state.resultados = None
    st.session_state.lineas = []
    st.session_state.lineas_df = None
    st.rerun()