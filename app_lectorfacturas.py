import io
import os
import shutil
import streamlit as st
import pandas as pd
import json
from google.oauth2 import service_account
from google.cloud import documentai_v1 as documentai
import re

# --- CONFIGURACIÓN ---
PROJECT_ID   = "772723410003"
LOCATION     = "us"
PROCESSOR_ID = "dff8117c158462cd"

# --- Autenticación con st.secrets ---
info = json.loads(st.secrets["google"]["credentials"])
creds = service_account.Credentials.from_service_account_info(info)
docai_client = documentai.DocumentProcessorServiceClient(credentials=creds)
processor_name = f"projects/{PROJECT_ID}/locations/{LOCATION}/processors/{PROCESSOR_ID}"

def parse_float_es(valor: str) -> float:
    if not valor:
        return 0.0
    limpia = re.sub(r"[^\d,\.]", "", valor).replace('.', '').replace(',', '.')
    try:
        return float(limpia)
    except ValueError:
        return 0.0

def procesar_factura_bytes(pdf_bytes, filename) -> dict:
    try:
        raw_doc = documentai.RawDocument(content=pdf_bytes, mime_type="application/pdf")
        req = documentai.ProcessRequest(name=processor_name, raw_document=raw_doc)
        res = docai_client.process_document(request=req)
        doc = res.document

        datos = {
            "Archivo": filename,
            "Proveedor": "",
            "Dirección": "",
            "Teléfono": "",
            "Nº Factura": "",
            "Fecha Emisión": "",
            "Nº Pedido": "",
            "Base Imponible": "",
            "IVA": "",
            "Importe Total": "",
            "CIF Proveedor": "",
            "Concepto": []
        }
        for e in doc.entities:
            text = e.mention_text or ""
            t = e.type_
            if t == "supplier_name":
                datos["Proveedor"] = text
            elif t == "supplier_address":
                datos["Dirección"] = text
            elif t == "supplier_phone":
                datos["Teléfono"] = text
            elif t == "supplier_tax_id":
                datos["CIF Proveedor"] = text
            elif t == "invoice_id":
                datos["Nº Factura"] = text
            elif t == "invoice_date":
                datos["Fecha Emisión"] = text
            elif t == "purchase_order":
                datos["Nº Pedido"] = text
            elif t == "net_amount":
                valor = parse_float_es(text)
                datos["Base Imponible"] = f"{valor:.2f}".replace('.', ',')
            elif t == "total_tax_amount":
                valor = parse_float_es(text)
                datos["IVA"] = f"{valor:.2f}".replace('.', ',')
            elif t == "total_amount":
                datos["Importe Total"] = text
            elif t == "line_item":
                for p in e.properties:
                    if p.type_.endswith("description"):
                        datos["Concepto"].append(p.mention_text or "")
        datos["Concepto"] = " | ".join(filter(None, datos["Concepto"]))
        return datos, None
    except Exception as e:
        return None, f"{filename}: {e}"

# --- Streamlit App State ---
if "uploaded_files" not in st.session_state:
    st.session_state.uploaded_files = []
if "resultados" not in st.session_state:
    st.session_state.resultados = None
if "errores" not in st.session_state:
    st.session_state.errores = None
if "procesado" not in st.session_state:
    st.session_state.procesado = False

split_dir = "split_temp"
os.makedirs(split_dir, exist_ok=True)

st.set_page_config(page_title="Lector de Facturas", layout="wide")
st.title("📄 Lector de Facturas con Document AI")

# Subida de archivos (en varias tandas)
uploaded_files = st.file_uploader(
    "Sube aquí tus facturas en PDF (puedes hacerlo en varias tandas antes de procesar)",
    type="pdf",
    accept_multiple_files=True,
    key="fileuploader"
)

# Guardar archivos subidos temporalmente
if uploaded_files:
    for uploaded in uploaded_files:
        temp_path = os.path.join(split_dir, uploaded.name)
        # Evita duplicados
        if not os.path.exists(temp_path):
            with open(temp_path, "wb") as f:
                f.write(uploaded.read())
    # Actualiza la lista interna de archivos
    st.session_state.uploaded_files = [
        os.path.join(split_dir, f) for f in os.listdir(split_dir) if f.endswith('.pdf')
    ]
    st.info(f"{len(st.session_state.uploaded_files)} archivos preparados para procesar.")

# Botón para procesar solo cuando lo pulse el usuario
if st.button("Procesar"):
    resultados = []
    errores = []
    total = len(st.session_state.uploaded_files)
    progreso = st.progress(0)
    procesadas = 0
    with st.spinner("Procesando facturas..."):
        for temp_path in st.session_state.uploaded_files:
            with open(temp_path, "rb") as f:
                pdf_bytes = f.read()
            datos, error = procesar_factura_bytes(pdf_bytes, os.path.basename(temp_path))
            if datos:
                resultados.append(datos)
            else:
                errores.append(error)
            procesadas += 1
            progreso.progress(procesadas / total if total else 1)
    progreso.progress(1.0)
    # Muestra resultados
    if resultados:
        df = pd.DataFrame(resultados)
        st.success(f"¡{len(resultados)} facturas procesadas correctamente!")
        st.session_state.resultados = df
    else:
        st.session_state.resultados = None
    st.session_state.errores = errores
    st.session_state.procesado = True

    # Limpieza automática de archivos temporales tras procesar
    shutil.rmtree(split_dir)
    os.makedirs(split_dir, exist_ok=True)
    st.session_state.uploaded_files = []

# Botón para limpiar resultados
if st.button("Limpiar resultados"):
    st.session_state.resultados = None
    st.session_state.errores = None
    st.session_state.procesado = False
    st.session_state.uploaded_files = []
    if os.path.exists(split_dir):
        shutil.rmtree(split_dir)
        os.makedirs(split_dir, exist_ok=True)
    st.info("Los resultados han sido limpiados. Puedes subir nuevos PDFs.")

# Mostrar resultados si hay
if st.session_state.procesado and st.session_state.resultados is not None:
    st.dataframe(st.session_state.resultados)
    # Descargar Excel
    towrite = io.BytesIO()
    st.session_state.resultados.to_excel(towrite, index=False, engine="openpyxl")
    towrite.seek(0)
    st.download_button(
        label="⬇️ Descargar Excel",
        data=towrite,
        file_name="facturas_extraidas.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
if st.session_state.procesado and st.session_state.errores:
    st.error("Se produjeron errores en algunos archivos:")
    for e in st.session_state.errores:
        st.write(e)
