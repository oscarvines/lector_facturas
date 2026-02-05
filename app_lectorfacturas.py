import io
import os
import shutil
import streamlit as st
import pandas as pd
import json
import re
from google.oauth2 import service_account
from google.cloud import documentai_v1 as documentai
from PyPDF2 import PdfReader, PdfWriter 

# --- CONFIGURACIÓN ---
PROJECT_ID   = "772723410003"
LOCATION     = "us"
PROCESSOR_ID = "e5c3f90497bd2e9f" 

# --- Autenticación con st.secrets ---
info = json.loads(st.secrets["google"]["credentials"])
creds = service_account.Credentials.from_service_account_info(info)
docai_client = documentai.DocumentProcessorServiceClient(credentials=creds)
processor_name = f"projects/{PROJECT_ID}/locations/{LOCATION}/processors/{PROCESSOR_ID}"

# --- FUNCIONES LÓGICA V3 ---

def parse_amount(valor: str) -> float:
    if not valor: return 0.0
    s = re.sub(r"[^\d,.\-]", "", str(valor))
    if not s: return 0.0
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s: s = s.replace(",", ".")
    try: return round(float(s), 2)
    except ValueError: return 0.0

def es_justificante_local(texto):
    """Lógica de ahorro V3: Detecta justificantes gratis sin bloquear nombres de bancos."""
    patrones = [
        r"detalle de orden", r"remesa", r"cuenta ordenante", r"cuenta beneficiario",
        r"justificante de pago", r"transferencia realizada", r"ejecutada", r"confirmado"
    ]
    # 'adeudo' se quita también si sospechas que puede venir en facturas, 
    # pero normalmente 'adeudo' define el documento bancario. Lo dejo fuera por si acaso.
    return any(re.search(p, texto.lower()) for p in patrones)

def llamar_a_document_ai(pdf_bytes):
    raw_doc = documentai.RawDocument(content=pdf_bytes, mime_type="application/pdf")
    req = documentai.ProcessRequest(name=processor_name, raw_document=raw_doc)
    res = docai_client.process_document(request=req)
    return res.document

# --- LÓGICA DE PROCESAMIENTO POR PÁGINAS (V3) ---

def procesar_archivo_v3(file_bytes, filename):
    pdf_reader = PdfReader(io.BytesIO(file_bytes))
    resultados_archivo = []
    
    for i, page in enumerate(pdf_reader.pages):
        texto_local = page.extract_text() or ""
        ref = f"{filename} (pág {i+1})"
        
        # 1. Filtro de Ahorro (Solo patrones muy específicos de justificantes)
        if es_justificante_local(texto_local):
            continue 
            
        # 2. Llamada a Google
        writer = PdfWriter()
        writer.add_page(page)
        with io.BytesIO() as buf:
            writer.write(buf)
            doc = llamar_a_document_ai(buf.getvalue())
            
            data = {
                "Archivo": ref, "Proveedor": "", "CIF_Proveedor": "",
                "Cliente": "", "CIF_Cliente": "", "Fecha": "", "Nº Factura": "",
                "Base Imponible": 0.0, "IVA": 0.0, "Total": 0.0, "Concepto": "", "Validación": ""
            }

            base_c, iva_c, total_c, descr = [], [], [], []

            for e in doc.entities:
                t, text = e.type_, e.mention_text or ""
                if t == "supplier_name": data["Proveedor"] = text.replace("\n", " ")
                elif t == "supplier_tax_id": data["CIF_Proveedor"] = text
                elif t == "customer_name": data["Cliente"] = text.replace("\n", " ")
                elif t == "customer_tax_id": data["CIF_Cliente"] = text
                elif t == "invoice_date": data["Fecha"] = text
                elif t == "invoice_id": data["Nº Factura"] = text
                elif t == "total_amount": total_c.append(text)
                elif t == "net_amount": base_c.append(text)
                elif t == "total_tax_amount": iva_c.append(text)
                elif t == "line_item":
                    for prop in e.properties:
                        if prop.type_ == "line_item/description": descr.append(prop.mention_text)

            data["Base Imponible"] = max([parse_amount(v) for v in base_c] or [0.0])
            data["IVA"] = max([parse_amount(v) for v in iva_c] or [0.0])
            data["Total"] = max([parse_amount(v) for v in total_c] or [0.0])
            data["Concepto"] = " | ".join(filter(None, [d.replace("\n", " ") for d in descr]))

            # 3. VALIDACIÓN DUAL MEJORADA
            base = data["Base Imponible"]
            iva = data["IVA"]
            total = data["Total"]
            
            diferencia_suma = abs((base + iva) - total)
            iva_esperado_21 = round(base * 0.21, 2)
            es_iva_21 = abs(iva - iva_esperado_21) < 0.05 

            if total <= 0:
                data["Validación"] = "SIN DATOS"
            elif diferencia_suma > 0.1:
                data["Validación"] = "ERROR SUMA"
            elif not es_iva_21:
                # Aquí caerán los justificantes de Cajamar/CaixaBank que pasen el filtro
                data["Validación"] = "REVISAR (No es 21%)"
            else:
                data["Validación"] = "CORRECTA"
            
            resultados_archivo.append(data)
            
    return resultados_archivo

# --- STREAMLIT UI ---

st.set_page_config(page_title="Lector Facturas V3", layout="wide")
st.title("📄 Lector de Facturas Pro (V3 - Validación 21%)")

if "uploaded_files_data" not in st.session_state:
    st.session_state.uploaded_files_data = {} 

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
    
    with st.spinner("Procesando y validando IVA..."):
        for i, (name, b) in enumerate(st.session_state.uploaded_files_data.items()):
            res_pdf = procesar_archivo_v3(b, name)
            todos_los_resultados.extend(res_pdf)
            progreso.progress((i + 1) / total_archivos)
            
    if todos_los_resultados:
        df = pd.DataFrame(todos_los_resultados)
        st.session_state.resultados = df
        st.success(f"Proceso finalizado.")
    else:
        st.warning("No se encontraron documentos válidos.")

if "resultados" in st.session_state and st.session_state.resultados is not None:
    def resaltar_validación(val):
        if val == 'CORRECTA': return ''
        return 'background-color: #ffcccc; color: #990000; font-weight: bold'

    st.dataframe(st.session_state.resultados.style.applymap(
        resaltar_validación, subset=['Validación']
    ))
    
    towrite = io.BytesIO()
    st.session_state.resultados.to_excel(towrite, index=False, engine="openpyxl")
    st.download_button(
        label="⬇️ Descargar Excel V3",
        data=towrite.getvalue(),
        file_name="facturas_v3.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

if st.button("🗑️ Limpiar Todo"):
    st.session_state.uploaded_files_data = {}
    st.session_state.resultados = None
    st.rerun()