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

    if "factura_sel_anterior" not in st.session_state:
        st.session_state.factura_sel_anterior = None

    if factura_sel != st.session_state.factura_sel_anterior:
        st.session_state.factura_sel_anterior = factura_sel
        if factura_sel:
            editor_key = f"editor_buffer_{factura_sel}"
            df_reset = st.session_state.lineas_df[
                st.session_state.lineas_df["id_factura"] == factura_sel
            ].copy()
            columnas_editor = [
                "id_linea",
                "id_factura",
                "proveedor",
                "cliente",
                "descripcion",
                "cantidad",
                "precio_unitario",
                "importe",
                "unidad",
                "aceptada",
                "confidence",
                "eliminar",
            ]
            df_lineas_sel = df_reset[columnas_editor].reset_index(drop=True)

            # --- LIMPIEZA PARA EVITAR None EN NUEVAS FILAS ---
            df_lineas_sel = df_lineas_sel.fillna({
                "proveedor": "",
                "cliente": "",
                "descripcion": "",
                "cantidad": 0,
                "precio_unitario": 0,
                "importe": 0,
                "unidad": "",
                "aceptada": True,
                "confidence": 1.0,
                "eliminar": False,
            })

            # Asegurar tipos correctos
            if "aceptada" in df_lineas_sel.columns:
                df_lineas_sel["aceptada"] = df_lineas_sel["aceptada"].astype(bool)
            if "eliminar" in df_lineas_sel.columns:
                df_lineas_sel["eliminar"] = df_lineas_sel["eliminar"].astype(bool)

            st.session_state[editor_key] = df_lineas_sel

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

        # Buffer estable para edición: evita el efecto de tener que editar dos veces
        editor_key = f"editor_buffer_{factura_sel}"
        columnas_editor = [
            "id_linea",
            "id_factura",
            "proveedor",
            "cliente",
            "descripcion",
            "cantidad",
            "precio_unitario",
            "importe",
            "unidad",
            "aceptada",
            "confidence",
            "eliminar",
        ]

        df_lineas_sel = df_lineas_sel[columnas_editor].reset_index(drop=True)

        if editor_key not in st.session_state:
            st.session_state[editor_key] = df_lineas_sel

        with st.form(key=f"form_lineas_{factura_sel}"):
            edited_df = st.data_editor(
                st.session_state[editor_key],
                key=f"editor_{factura_sel}",
                use_container_width=True,
                num_rows="dynamic",
                hide_index=True,
                column_config={
                    "id_linea": st.column_config.TextColumn(disabled=True),
                    "id_factura": st.column_config.TextColumn(disabled=True),
                    "proveedor": st.column_config.TextColumn(),
                    "cliente": st.column_config.TextColumn(),
                    "descripcion": st.column_config.TextColumn(),
                    "cantidad": st.column_config.NumberColumn(),
                    "precio_unitario": st.column_config.NumberColumn(),
                    "importe": st.column_config.NumberColumn(),
                    "unidad": st.column_config.TextColumn(),
                    "aceptada": st.column_config.CheckboxColumn(default=True),
                    "eliminar": st.column_config.CheckboxColumn(default=False),
                    "confidence": st.column_config.NumberColumn(disabled=True),
                },
            )

            aplicar_cambios = st.form_submit_button("💾 Aplicar cambios en líneas")

        if aplicar_cambios:
            nuevas_filas = []
            for i, row in edited_df.iterrows():

                # Si está marcada para eliminar, la saltamos
                if row.get("eliminar", False):
                    continue

                nuevas_filas.append({
                    "id_linea": row.get("id_linea") or f"{factura_sel}_{i}",
                    "id_factura": factura_sel,
                    "proveedor": row.get("proveedor", ""),
                    "cliente": row.get("cliente", ""),
                    "descripcion": row.get("descripcion", ""),
                    "cantidad": row.get("cantidad", 0),
                    "precio_unitario": row.get("precio_unitario", 0),
                    "importe": row.get("importe", 0),
                    "unidad": row.get("unidad", ""),
                    "aceptada": row.get("aceptada", True),
                    "confidence": row.get("confidence", 1.0),
                    "eliminar": False,
                })

            # Reemplazar solo las líneas de la factura seleccionada
            st.session_state.lineas_df = st.session_state.lineas_df[
                st.session_state.lineas_df["id_factura"] != factura_sel
            ]
            st.session_state.lineas_df = pd.concat(
                [st.session_state.lineas_df, pd.DataFrame(nuevas_filas)],
                ignore_index=True,
            )

            # Actualizar buffer del editor con el estado ya consolidado
            df_nuevas = pd.DataFrame(nuevas_filas)

            if "eliminar" not in df_nuevas.columns:
                df_nuevas["eliminar"] = False

            st.session_state[editor_key] = df_nuevas[columnas_editor]

            # Recalcular total aceptado solo para esta factura
            suma_aceptadas = pd.DataFrame(nuevas_filas).loc[
                pd.DataFrame(nuevas_filas)["aceptada"] == True, "importe"
            ].sum()
            st.session_state.resultados.loc[
                st.session_state.resultados["id_factura"] == factura_sel, "total_aceptado"
            ] = suma_aceptadas

            st.rerun()

        # Mostrar el total aceptado persistido
        total_persistido = st.session_state.resultados.loc[
            st.session_state.resultados["id_factura"] == factura_sel, "total_aceptado"
        ].iloc[0]
        st.markdown(
            f"**Total aceptado** para la factura `{factura_sel}`: {float(total_persistido):.2f}"
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