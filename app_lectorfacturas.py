import io
import json
import streamlit as st
import pandas as pd
from google.oauth2 import service_account
from google.cloud import documentai_v1 as documentai

# Import sin paquete (compatible con Streamlit / ejecución directa)
from procesador_facturas import init_docai_client, procesar_archivo

# --- CONFIGURACIÓN ---
PROJECT_ID = "772723410003"
LOCATION = "us"
PROCESSOR_ID = "dff8117c158462cd"

# --- Autenticación con st.secrets ---
info = json.loads(st.secrets["google"]["credentials"])
creds = service_account.Credentials.from_service_account_info(info)
docai_client = init_docai_client(creds)
PROCESSOR_VERSION_ID = "e4fb17a2603c6087"
processor_name = f"projects/{PROJECT_ID}/locations/{LOCATION}/processors/{PROCESSOR_ID}/processorVersions/{PROCESSOR_VERSION_ID}"

# --- STREAMLIT UI ---

st.set_page_config(page_title="Lector de Facturas Pro", layout="wide")
st.title("📄 Lector de Facturas (V4)")

# Initialize session state containers
if "uploaded_files_data" not in st.session_state:
    # Dictionary mapping filename -> file bytes
    st.session_state.uploaded_files_data = {}
if "lineas" not in st.session_state:
    # List of all line items across processed PDFs
    st.session_state.lineas = []
if "resultados" not in st.session_state:
    # DataFrame of invoice headers
    st.session_state.resultados = None
if "lineas_df" not in st.session_state:
    # DataFrame of line items
    st.session_state.lineas_df = None

# File uploader: allow multiple PDFs
uploaded_files = st.file_uploader(
    "Sube tus PDFs", type="pdf", accept_multiple_files=True, key="file_uploader"
)

# Store uploaded files in session state
if uploaded_files:
    for f in uploaded_files:
        if f.name not in st.session_state.uploaded_files_data:
            st.session_state.uploaded_files_data[f.name] = f.read()
    st.info(f"Archivos listos: {len(st.session_state.uploaded_files_data)}")

# Button to start processing
if st.button("🚀 Procesar"):
    todos_los_facturas = []
    # Reset line items before processing new batch
    st.session_state.lineas = []
    total_archivos = len(st.session_state.uploaded_files_data)
    progreso = st.progress(0)
    with st.spinner("Analizando PDFs..."):
        for i, (name, b) in enumerate(st.session_state.uploaded_files_data.items()):
            facturas, lineas = procesar_archivo(b, name, docai_client, processor_name)
            todos_los_facturas.extend(facturas)
            st.session_state.lineas.extend(lineas)
            progreso.progress((i + 1) / total_archivos)
    if todos_los_facturas:
        df_facturas = pd.DataFrame(todos_los_facturas)
        df_lineas = pd.DataFrame(st.session_state.lineas)
        st.session_state.resultados = df_facturas
        st.session_state.lineas_df = df_lineas
        st.success(
            f"Proceso finalizado. Se extrajeron {len(df_facturas)} factura(s) y {len(df_lineas)} línea(s)."
        )
    else:
        st.warning("No se encontraron facturas válidas en los PDFs seleccionados.")

# Display results if available
if st.session_state.resultados is not None:
    st.subheader("📄 Facturas")
    st.dataframe(st.session_state.resultados)

    # Selector de factura
    factura_ids = st.session_state.resultados["id_factura"].tolist()
    factura_sel = st.selectbox(
        "Selecciona una factura para ver su desglose de líneas",
        factura_ids,
    ) if factura_ids else None

    if factura_sel:
        st.subheader("📦 Líneas de la factura seleccionada (editable)")

        # --- SUMATORIO ACTUAL DE LÍNEAS ---
        total_lineas_actual = st.session_state.lineas_df[
            (st.session_state.lineas_df["id_factura"] == factura_sel) &
            (st.session_state.lineas_df["aceptada"] == True)
        ]["importe"].sum()

        st.metric(
            label="💰 Total líneas aceptadas (actual)",
            value=f"{total_lineas_actual:.2f}"
        )

        # Filtrar las líneas por la factura seleccionada
        df_lineas_sel = st.session_state.lineas_df[
            st.session_state.lineas_df["id_factura"] == factura_sel
        ].copy()

        # Asegurar índice estable (clave para evitar doble click / rerun raro)
        df_lineas_sel = df_lineas_sel.reset_index()

        edited_df = st.data_editor(
            df_lineas_sel,
            key=f"editor_{factura_sel}",
            use_container_width=True,
            num_rows="dynamic",
            column_config={
                "index": st.column_config.NumberColumn(disabled=True),
                "id_linea": st.column_config.TextColumn(disabled=True),
                "id_factura": st.column_config.TextColumn(disabled=True),
                "confidence": st.column_config.NumberColumn(disabled=True),
                "descripcion": st.column_config.TextColumn(),
                "cantidad": st.column_config.NumberColumn(),
                "precio_unitario": st.column_config.NumberColumn(),
                "importe": st.column_config.NumberColumn(),
                "unidad": st.column_config.TextColumn(),
                "aceptada": st.column_config.CheckboxColumn()
            }
        )

        # --- SINCRONIZACIÓN COMPLETA (evita doble edición) ---
        # Eliminamos filas actuales de la factura y reconstruimos desde el editor
        st.session_state.lineas_df = st.session_state.lineas_df[
            st.session_state.lineas_df["id_factura"] != factura_sel
        ]

        nuevas_filas = []

        for i, row in edited_df.iterrows():
            nuevas_filas.append({
                "id_linea": f"{factura_sel}_{i}",
                "id_factura": factura_sel,
                "proveedor": row.get("proveedor", ""),
                "cliente": row.get("cliente", ""),
                "descripcion": row.get("descripcion", ""),
                "cantidad": row.get("cantidad", 0),
                "precio_unitario": row.get("precio_unitario", 0),
                "importe": row.get("importe", 0),
                "unidad": row.get("unidad", ""),
                "aceptada": row.get("aceptada", True),
                "confidence": row.get("confidence", 1.0)
            })

        st.session_state.lineas_df = pd.concat(
            [st.session_state.lineas_df, pd.DataFrame(nuevas_filas)],
            ignore_index=True
        )

        # Recalcular el total aceptado solo para esta factura
        suma_aceptadas = edited_df.loc[edited_df["aceptada"] == True, "importe"].sum()
        # Actualizar el DataFrame de facturas
        st.session_state.resultados.loc[
            st.session_state.resultados["id_factura"] == factura_sel, "total_aceptado"
        ] = suma_aceptadas
        # Mostrar las líneas seleccionadas para referencia
        st.markdown(
            f"**Total aceptado** para la factura `{factura_sel}`: {suma_aceptadas:.2f}"
        )

    # Botón para descargar los datos en Excel
    st.subheader("📥 Exportar resultados")
    towrite = io.BytesIO()
    with pd.ExcelWriter(towrite, engine="openpyxl") as writer:
        st.session_state.resultados.to_excel(writer, sheet_name="Facturas", index=False)
        st.session_state.lineas_df.to_excel(writer, sheet_name="Lineas", index=False)
    st.download_button(
        label="⬇️ Descargar Excel",
        data=towrite.getvalue(),
        file_name="facturas_resultado.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# Botón para limpiar la sesión
if st.button("🗑️ Limpiar Todo"):
    st.session_state.uploaded_files_data = {}
    st.session_state.resultados = None
    st.session_state.lineas = []
    st.session_state.lineas_df = None
    st.success("Sesión limpiada. Sube nuevos archivos para comenzar.")