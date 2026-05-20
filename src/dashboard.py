import streamlit as st
import pandas as pd
import plotly.express as px
import psycopg2
import os
from pathlib import Path

# --- CONFIGURATION ---
st.set_page_config(page_title="Job Market Analytics", layout="wide")


def load_dotenv_file(path: str = ".env"):
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(
            key.strip(), value.strip().replace('"', "").replace("'", "")
        )


def get_data():
    load_dotenv_file()
    try:
        conn = psycopg2.connect(
            host=os.environ.get("PGHOST", "localhost"),
            port=int(os.environ.get("PGPORT", "5433")),
            dbname=os.environ.get("PGDATABASE", "airflow"),
            user=os.environ.get("PGUSER", "airflow"),
            password=os.environ.get("PGPASSWORD", "airflow"),
        )
        df = pd.read_sql("SELECT * FROM offers_unified", conn)
        conn.close()
        return df
    except Exception as e:
        st.error(f"Erreur de connexion : {e}")
        return pd.DataFrame()


# --- CHARGEMENT ---
df = get_data()

if df.empty:
    st.warning("⚠️ Aucune donnée trouvée. Lancez le pipeline ETL.")
else:
    # Nettoyage minimal
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")

    # --- SIDEBAR (Filtres) ---
    st.sidebar.header("🔍 Filtres")
    sources = st.sidebar.multiselect(
        "Sources", sorted(df["source"].unique()), default=df["source"].unique()
    )
    contracts = st.sidebar.multiselect(
        "Types de contrat",
        sorted(df["contract_type"].unique()),
        default=df["contract_type"].unique(),
    )
    exp_levels = st.sidebar.multiselect(
        "Niveau d'expérience",
        sorted(df["experience_level"].unique()),
        default=df["experience_level"].unique(),
    )

    # Filtre ville (ajouté à l'ancienne version)
    available_cities = df["city"].value_counts().head(50).index.tolist()
    cities = st.sidebar.multiselect("Villes (Top 50)", sorted(available_cities))

    mask = (
        df["source"].isin(sources)
        & df["contract_type"].isin(contracts)
        & df["experience_level"].isin(exp_levels)
    )
    if cities:
        mask = mask & df["city"].isin(cities)

    df_f = df[mask]

    # --- HEADER ---
    st.title("💼 Analyse du Marché de l'Emploi")
    st.markdown(f"Analyse de **{len(df_f)}** offres d'emploi nettoyées.")

    # --- KPIs ---
    kpi1, kpi2, kpi3 = st.columns(3)
    with kpi1:
        st.metric("Total Offres", len(df_f))
    with kpi2:
        top_job = (
            df_f["title_clean"].mode()[0] if not df_f["title_clean"].empty else "N/A"
        )
        st.metric(
            "Métier le plus recherché",
            top_job.capitalize() if top_job != "N/A" else "N/A",
        )
    with kpi3:
        avg_sal = df_f["salary_annual"].mean()
        st.metric(
            "Salaire Annuel Moyen (Est.)",
            f"{avg_sal:,.0f} €" if not pd.isna(avg_sal) else "N/A",
        )

    st.divider()

    # --- GRAPHIQUES ---
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("🌐 Répartition par Source")
        fig_source = px.pie(
            df_f,
            names="source",
            hole=0.4,
            color_discrete_sequence=px.colors.qualitative.Safe,
        )
        st.plotly_chart(fig_source, use_container_width=True)

    with c2:
        st.subheader("🏢 Top 10 Recruteurs")
        top_comp = df_f["company_name"].value_counts().head(10).reset_index()
        fig_comp = px.bar(
            top_comp,
            x="count",
            y="company_name",
            orientation="h",
            color="count",
            color_continuous_scale="Blues",
        )
        st.plotly_chart(fig_comp, use_container_width=True)

    c3, c4 = st.columns(2)

    with c3:
        st.subheader("🏙️ Top 10 Villes")
        top_cities = df_f["city"].value_counts().head(10).reset_index()
        fig_city = px.bar(
            top_cities,
            x="city",
            y="count",
            color="count",
            color_continuous_scale="Viridis",
        )
        st.plotly_chart(fig_city, use_container_width=True)

    with c4:
        st.subheader("🎓 Expérience vs Salaire")
        sal_exp = (
            df_f.groupby("experience_level")["salary_annual"]
            .mean()
            .sort_values()
            .reset_index()
        )
        fig_sal_exp = px.bar(
            sal_exp, x="experience_level", y="salary_annual", color="experience_level"
        )
        st.plotly_chart(fig_sal_exp, use_container_width=True)

    c5, c6 = st.columns(2)

    with c5:
        st.subheader("💰 Répartition des Salaires")
        if df_f["salary_annual"].dropna().empty:
            st.info("Données de salaire insuffisantes.")
        else:
            fig_sal = px.histogram(
                df_f,
                x="salary_annual",
                nbins=15,
                color_discrete_sequence=["#1E3A8A"],
                labels={"salary_annual": "Salaire Annuel (€)"},
            )
            st.plotly_chart(fig_sal, use_container_width=True)

    with c6:
        st.subheader("🤝 Mix des Contrats")
        fig_cont = px.pie(
            df_f,
            names="contract_type",
            hole=0.4,
            color_discrete_sequence=px.colors.qualitative.Pastel,
        )
        st.plotly_chart(fig_cont, use_container_width=True)

    # --- DATA TABLE ---
    st.divider()
    st.subheader("📋 Explorateur d'Offres")
    st.dataframe(
        df_f[
            [
                "created_at",
                "title_clean",
                "company_name",
                "city",
                "experience_level",
                "salary_annual",
                "external_url",
            ]
        ].sort_values("created_at", ascending=False),
        column_config={
            "external_url": st.column_config.LinkColumn("Lien"),
            "salary_annual": st.column_config.NumberColumn(
                "Salaire Est. (€)", format="%d €"
            ),
        },
        use_container_width=True,
        hide_index=True,
    )
