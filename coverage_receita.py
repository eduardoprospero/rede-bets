# -*- coding: utf-8 -*-
"""
coverage_receita.py - descobre POR QUE poucas bets foram achadas na base da Receita.

Ele não confia no banco SQLite: varre os .zip brutos e responde, para cada raiz de CNPJ da lista SPA,
em quais arquivos ela aparece. Assim separa três cenários:

  A) a raiz NÃO está em nenhum zip  -> zips incompletos/parciais, competência que não traz a
                                        empresa, ou raiz errada na lista SPA;
  B) a raiz ESTÁ nos zips mas não no banco -> falha na carga (03 'cadastro'), arquivo de raízes errado
                                        ou banco de outra execução;
  C) a estrutura dos arquivos é diferente do esperado (coluna 1 não parece CNPJ básico, nº de colunas...).

Uso
---
    python coverage_receita.py --rapido     # só estrutura: 1º bloco de cada arquivo (segundos/minutos)
    python coverage_receita.py              # varredura completa de empresas, estabelecimentos e sócios
    python coverage_receita.py --tipos empresas   # só um tipo
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pandas as pd

import config as cfg

# Tenta importar DIR_SPA do common, se não conseguir, define um fallback padrão
try:
    from common import DIR_SPA
except ImportError:
    DIR_SPA = Path("dados/spa")

# reaproveita as funções de leitura do script 03 
_spec = importlib.util.spec_from_file_location("carregar", Path(__file__).with_name("load_receita.py"))
carregar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(carregar)


def varrer(pasta: Path, tipo: str, raizes: set[str], rapido: bool, chunk: int, encoding: str | None) -> tuple[list[dict], set[str]]:
    linhas_rel, achadas = [], set()
    for z in carregar.zips_do_tipo(pasta, tipo):
        rel = {"arquivo": z.name, "mb": round(z.stat().st_size / 1e6, 1), "linhas": 0, "raizes_da_spa": 0,
               "col1_formato_ok_%": None, "min_col1": None, "max_col1": None, "obs": ""}
        hits: set[str] = set()
        ok = tot = 0
        try:
            for bloco in carregar.iter_tabela([z], cfg.COLUNAS[tipo], chunk, encoding):
                s = bloco["cnpj_basico"]
                rel["linhas"] += len(bloco)
                tot += len(s)
                ok += int(s.str.fullmatch(r"[0-9A-Z]{8}").sum())
                hits |= set(s[s.isin(raizes)])
                mn, mx = s.min(), s.max()
                rel["min_col1"] = mn if rel["min_col1"] is None else min(rel["min_col1"], mn)
                rel["max_col1"] = mx if rel["max_col1"] is None else max(rel["max_col1"], mx)
                if rapido:
                    rel["obs"] = "só 1º bloco"
                    break
        except zipfile.BadZipFile:
            rel["obs"] = "ZIP CORROMPIDO/INCOMPLETO"
        except Exception as e:  # noqa: BLE001
            rel["obs"] = f"erro: {type(e).__name__}: {str(e)[:80]}"
        rel["raizes_da_spa"] = len(hits)
        rel["col1_formato_ok_%"] = round(100 * ok / tot, 2) if tot else None
        achadas |= hits
        linhas_rel.append(rel)
    return linhas_rel, achadas


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mes"); ap.add_argument("--pasta", type=Path)
    
    # ADAPTAÇÃO: Aponta para a pasta e o arquivo que geramos na nossa nova lógica da SPA
    ap.add_argument("--raizes", type=Path, default=DIR_SPA / "bets_empresas.csv")
    
    ap.add_argument("--tipos", nargs="+", default=["empresas", "estabelecimentos", "socios"],
                    choices=["empresas", "estabelecimentos", "socios"])
    ap.add_argument("--rapido", action="store_true", help="lê só o 1º bloco de cada arquivo (checa estrutura)")
    ap.add_argument("--chunk", type=int, default=carregar.CHUNK_PADRAO)
    ap.add_argument("--encoding")
    args = ap.parse_args()

    # Fallback caso o script da SPA tenha gerado o arquivo "integrada" em vez do padrão
    arquivo_spa = args.raizes
    if not arquivo_spa.exists() and (DIR_SPA / "bets_empresas_integrada.csv").exists():
        arquivo_spa = DIR_SPA / "bets_empresas_integrada.csv"
        
    if not arquivo_spa.exists():
         sys.exit(f"Arquivo de empresas da SPA não encontrado em {arquivo_spa}. Rode o script da SPA primeiro.")

    pasta = carregar.resolver_pasta(args.mes, args.pasta)
    
    # Lê a tabela da SPA e garante que a coluna cnpj_basico exista e esteja limpa
    df_spa = pd.read_csv(arquivo_spa, dtype=str)
    if "cnpj_basico" not in df_spa.columns and "cnpj" in df_spa.columns:
        df_spa["cnpj_basico"] = df_spa["cnpj"].str[:8]
    
    raizes = set(df_spa["cnpj_basico"].dropna().str.strip())
    
    print(f"Competência: {pasta.name} | raízes da SPA: {len(raizes)}")
    print("\n[1] Arquivos na pasta")
    for z in sorted(pasta.glob("*")):
        print(f"  {z.name:<45} {z.stat().st_size / 1e6:>10.1f} MB  tipo={cfg.classificar_arquivo(z.name)}")
    manifesto = pasta / "manifesto.json"
    if manifesto.exists():
        n_man = len(json.loads(manifesto.read_text(encoding="utf-8")).get("arquivos", []))
        print(f"  manifesto.json lista {n_man} arquivos baixados")

    resumo: dict[str, set[str]] = {}
    for tipo in args.tipos:
        print(f"\n[2] Varredura: {tipo}" + (" (rápida)" if args.rapido else ""))
        rel, achadas = varrer(pasta, tipo, raizes, args.rapido, args.chunk, args.encoding)
        resumo[tipo] = achadas
        print(pd.DataFrame(rel).to_string(index=False))
        print(f"  -> raízes da SPA achadas em {tipo}: {len(achadas)} de {len(raizes)}")

    if not args.rapido and "empresas" in resumo:
        ausentes = sorted(raizes - resumo["empresas"])
        print(f"\n[3] Raízes da SPA ausentes de TODOS os zips de empresas: {len(ausentes)}")
        print("  ", ausentes[:15], "..." if len(ausentes) > 15 else "")

    print("\n[4] Banco SQLite")
    if cfg.DB_PATH.exists():
        con = sqlite3.connect(cfg.DB_PATH)
        for t in ("empresas", "estabelecimentos", "socios"):
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                print(f"  {t}: {n:,} linhas")
            except sqlite3.Error:
                print(f"  {t}: tabela inexistente")
        print("  meta:", dict(con.execute("SELECT chave, valor FROM meta").fetchall()))
        try:
            print("\n  Tamanho do documento por tipo de sócio (tabela socios inteira):")
            print(pd.read_sql_query(
                "SELECT identificador_socio AS tipo, LENGTH(TRIM(cnpj_cpf_socio)) AS doc_len, COUNT(*) AS n FROM socios GROUP BY 1,2 ORDER BY 1,3 DESC", con
            ).to_string(index=False))
            print("\n  Exemplos de sócio PJ com documento != 14 caracteres:")
            print(pd.read_sql_query(
                "SELECT cnpj_basico, nome_socio, cnpj_cpf_socio, LENGTH(TRIM(cnpj_cpf_socio)) AS doc_len FROM socios "
                "WHERE identificador_socio='1' AND LENGTH(TRIM(cnpj_cpf_socio))<>14 LIMIT 8", con).to_string(index=False))
        except Exception as e:  # noqa: BLE001
            print("  (não foi possível checar documentos:", e, ")")
        if "empresas" in resumo and not args.rapido:
            no_banco = {r[0] for r in con.execute("SELECT cnpj_basico FROM empresas")}
            print(f"\n  Achadas nos zips mas AUSENTES do banco (cenário B): {len(resumo['empresas'] - no_banco)}")
        con.close()
    else:
        print("  banco não existe.")


if __name__ == "__main__":
    main()