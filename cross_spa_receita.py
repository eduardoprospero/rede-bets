# -*- coding: utf-8 -*-
"""
cross_spa_receita.py - cruza a lista da SPA com a base da Receita (nível 1) e diagnostica a qualidade.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

import config as cfg

# Tenta importar DIR_SPA do common, se não conseguir, define um fallback padrão
try:
    from common import DIR_SPA
except ImportError:
    DIR_SPA = Path("dados/spa")


def normaliza_nome_local(nome: str) -> str:
    """Função fallback para normalização caso não exista no cfg."""
    if not isinstance(nome, str):
        return ""
    import unicodedata
    import re
    nome = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", nome.lower()).strip()


def main() -> None:
    # 1. Busca os arquivos gerados pela lógica da SPA
    possiveis_arquivos = [
        DIR_SPA / "bets_empresas_integrada.csv",
        DIR_SPA / "bets_empresas.csv",
        cfg.DIR_PROC / "spa_empresas.csv" # fallback legado
    ]
    
    spa_csv = next((f for f in possiveis_arquivos if f.exists()), None)
    
    if not spa_csv:
        sys.exit(f"Arquivo da SPA não encontrado. Rode o script da SPA antes. Procurado em: {[str(p) for p in possiveis_arquivos]}")

    if not cfg.DB_PATH.exists():
        sys.exit("Banco SQLite não encontrado. Rode 03_carregar_receita.py antes.")

    cfg.DIR_PROC.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(cfg.DB_PATH)
    tabelas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    faltam = {"socios", "empresas", "estabelecimentos", "dom_naturezas", "dom_qualificacoes"} - tabelas
    if faltam:
        sys.exit(f"Tabelas ausentes no banco: {sorted(faltam)}. Rode os subcomandos do script 03.")

    # 2. Carrega a tabela no banco
    spa = pd.read_csv(spa_csv, dtype=str)
    
    spa.columns = spa.columns.str.lower().str.strip()  # Padroniza tudo para minúsculo
    spa = spa.loc[:, ~spa.columns.duplicated()]        # Descarta colunas com nomes repetidos

    # Garante que as colunas essenciais existam
    if "cnpj_basico" not in spa.columns and "cnpj" in spa.columns:
        spa["cnpj_basico"] = spa["cnpj"].str[:8]
    if "empresa" not in spa.columns and "razao_social" in spa.columns:
        spa["empresa"] = spa["razao_social"]

    spa.to_sql("spa_empresas", conn, if_exists="replace", index=False)
    
    meta_rows = conn.execute("SELECT chave, valor FROM meta").fetchall() if "meta" in tabelas else []
    competencia = dict(meta_rows).get("competencia", "Desconhecida")

    # ---- nível 1: sócios diretos de cada bet --------------------------------------------------
    nivel1 = pd.read_sql_query(
        """
        SELECT  e.cnpj              AS cnpj_bet,
                e.empresa           AS razao_social_spa,
                s.cnpj_basico       AS bet_basico,
                CASE s.identificador_socio WHEN '1' THEN 'PJ' WHEN '2' THEN 'PF' WHEN '3' THEN 'ESTRANGEIRO' ELSE '?' END
                                    AS tipo_socio,
                s.nome_socio, s.cnpj_cpf_socio, s.socio_chave, s.socio_basico,
                s.qualificacao_socio, q.descricao AS qualificacao_desc,
                s.data_entrada_sociedade, s.pais, s.faixa_etaria,
                s.representante_legal, s.nome_representante
        FROM spa_empresas e
        JOIN socios s           ON s.cnpj_basico = e.cnpj_basico
        LEFT JOIN dom_qualificacoes q ON q.codigo = s.qualificacao_socio
        ORDER BY e.cnpj, s.nome_socio
        """,
        conn,
    )
    nivel1.to_csv(cfg.DIR_PROC / "nivel1_socios.csv", index=False, encoding="utf-8-sig")

    # ---- diagnóstico por bet ------------------------------------------------------------------
    diag = pd.read_sql_query(
        """
        SELECT  e.cnpj, e.cnpj_basico, e.empresa AS razao_spa, e.n_autorizacoes, e.n_marcas,
                em.razao_social AS razao_rfb, em.natureza_juridica,
                n.descricao AS natureza_desc, em.capital_social,
                (SELECT situacao_cadastral FROM estabelecimentos est
                  WHERE est.cnpj_basico = e.cnpj_basico AND est.cnpj_ordem = substr(e.cnpj, 9, 4)) AS situacao_cadastral,
                (SELECT COUNT(*) FROM socios s WHERE s.cnpj_basico = e.cnpj_basico)                  AS n_socios,
                (SELECT COUNT(*) FROM socios s WHERE s.cnpj_basico = e.cnpj_basico AND s.identificador_socio='1') AS n_socios_pj,
                (SELECT COUNT(*) FROM socios s WHERE s.cnpj_basico = e.cnpj_basico AND s.identificador_socio='2') AS n_socios_pf,
                (SELECT COUNT(*) FROM socios s WHERE s.cnpj_basico = e.cnpj_basico AND s.identificador_socio='3') AS n_socios_estrangeiros
        FROM spa_empresas e
        LEFT JOIN empresas em        ON em.cnpj_basico = e.cnpj_basico
        LEFT JOIN dom_naturezas n    ON n.codigo = em.natureza_juridica
        ORDER BY e.empresa
        """,
        conn,
    )
    
    diag["achada_na_receita"] = diag["razao_rfb"].notna()
    
    naturezas_sa = getattr(cfg, "NATUREZAS_SA", ["2046", "2054"])
    diag["eh_sa"] = diag["natureza_juridica"].isin(naturezas_sa)
    
    diag["ativa"] = diag["situacao_cadastral"].str.lstrip("0") == "2"
    
    norm_func = getattr(cfg, "normaliza_nome", normaliza_nome_local)
    diag["nome_confere"] = diag.apply(
        lambda r: norm_func(str(r["razao_spa"])) == norm_func(str(r["razao_rfb"])), axis=1
    )
    
    diag["tipo_qsa"] = diag["eh_sa"].map({True: "administradores (S.A.)", False: "sócios/cotistas"}).where(diag["achada_na_receita"])
    diag.to_csv(cfg.DIR_PROC / "diagnostico_bets.csv", index=False, encoding="utf-8-sig")

    # ---- checagens de formato dos documentos --------------------------------------------------
    doc = pd.read_sql_query(
        "SELECT identificador_socio AS tipo, LENGTH(TRIM(cnpj_cpf_socio)) AS doc_len, COUNT(*) AS n FROM socios "
        "WHERE cnpj_basico IN (SELECT cnpj_basico FROM spa_empresas) GROUP BY 1, 2", conn)

    pf_multi = pd.read_sql_query(
        """SELECT socio_chave, COUNT(DISTINCT s.cnpj_basico) AS n_bets
           FROM socios s JOIN spa_empresas e ON e.cnpj_basico = s.cnpj_basico
           WHERE s.socio_chave LIKE 'PF:%' GROUP BY socio_chave HAVING n_bets > 1""", conn)
           
    pj_multi = pd.read_sql_query(
        """SELECT socio_basico, COUNT(DISTINCT s.cnpj_basico) AS n_bets
           FROM socios s JOIN spa_empresas e ON e.cnpj_basico = s.cnpj_basico
           WHERE s.socio_basico <> '' AND s.socio_basico IS NOT NULL 
           GROUP BY socio_basico HAVING n_bets > 1""", conn)

    resumo = {
        "competencia_receita": competencia,
        "cnpjs_spa": int(len(diag)),
        "achados_na_receita": int(diag["achada_na_receita"].sum()),
        "nao_achados": diag.loc[~diag["achada_na_receita"], "cnpj"].tolist(),
        "ativas": int(diag["ativa"].sum()),
        "nao_ativas": diag.loc[diag["achada_na_receita"] & ~diag["ativa"], "cnpj"].tolist(),
        "sociedades_anonimas": int(diag["eh_sa"].sum()),
        "bets_sem_nenhum_socio_no_qsa": diag.loc[diag["achada_na_receita"] & (diag["n_socios"] == 0), "cnpj"].tolist(),
        "nomes_que_nao_conferem": diag.loc[diag["achada_na_receita"] & ~diag["nome_confere"], "cnpj"].tolist(),
        "linhas_nivel1": int(len(nivel1)),
        "socios_por_tipo": nivel1["tipo_socio"].value_counts().to_dict(),
        "socios_pj_distintos_a_expandir": int(nivel1.loc[nivel1["tipo_socio"] == "PJ", "socio_basico"].nunique()),
        "tamanho_documento_por_tipo": doc.to_dict(orient="records"),
        "pf_chaves_em_mais_de_uma_bet": int(len(pf_multi)),
        "pj_em_mais_de_uma_bet": int(len(pj_multi)),
        "alertas": [],
    }

    a = resumo["alertas"]
    if resumo["nao_achados"]:
        a.append("Há CNPJs da SPA que não aparecem em empresas: competência antiga, CNPJ novo ou erro de raiz.")
    if resumo["sociedades_anonimas"]:
        a.append("Há S.A. entre as bets: o QSA delas mostra administradores, não acionistas. Buscar estatuto/Junta/CVM.")
    if not doc.empty:
        pf_ruim = doc[(doc["tipo"] == "2") & (~doc["doc_len"].isin([6, 11, 14]))] 
        pj_ruim = doc[(doc["tipo"] == "1") & (doc["doc_len"] != 14)]
        if len(pf_ruim):
            a.append("Documentos de sócios PF com tamanho incomum: revise a carga.")
        if len(pj_ruim):
            a.append("CNPJs de sócios PJ com tamanho diferente de 14: revise a carga.")
    if resumo["bets_sem_nenhum_socio_no_qsa"]:
        a.append("Há bets sem nenhum registro em socios: confira o JOIN e a competência.")

    (cfg.DIR_PROC / "diagnostico_resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== Diagnóstico nível 1 ===")
    print(f"Competência da Receita: {competencia}")
    print(f"CNPJs na lista SPA: {resumo['cnpjs_spa']} | achados na Receita: {resumo['achados_na_receita']} | ativos: {resumo['ativas']}")
    print(f"S.A. (QSA = administradores): {resumo['sociedades_anonimas']}")
    print(f"Sócios de nível 1 por tipo: {resumo['socios_por_tipo']}")
    print(f"Sócios PJ distintos a expandir: {resumo['socios_pj_distintos_a_expandir']}")
    print(f"PJ presentes em >1 bet: {resumo['pj_em_mais_de_uma_bet']} | PF (nome+6 dígitos) em >1 bet: {resumo['pf_chaves_em_mais_de_uma_bet']}")
    for msg in a:
        print("ALERTA:", msg)
    print(f"Arquivos em {cfg.DIR_PROC}")
    conn.close()


if __name__ == "__main__":
    main()