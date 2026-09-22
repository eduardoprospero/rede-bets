from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd

from common import DIR_SPA, cnpj14, cnpj_valido, erro, log, normaliza_id, raiz_cnpj


def decodificar(dados: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return dados.decode(enc), enc
        except UnicodeDecodeError:
            continue
    erro("Não consegui decodificar o arquivo.")


def ler_tabela(caminho: Path, dados: bytes) -> tuple[pd.DataFrame, dict]:
    info: dict = {}
    
    if caminho.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(io.BytesIO(dados), dtype=str, keep_default_na=False, header=None)
        info["formato"] = "excel"
    else:
        texto, enc = decodificar(dados)
        amostra = "\n".join(texto.splitlines()[:5])
        sep = max([";", ",", "\t", "|"], key=amostra.count)
        if amostra.count(sep) == 0: 
            sep = ','
        info.update(formato="csv", encoding=enc, separador=sep)
        df = pd.read_csv(io.StringIO(texto), sep=sep, dtype=str, keep_default_na=False, header=None)
        
    # Remove marcações nulas para evitar falsos positivos
    df = df.fillna("")
    
    header_idx = 0
    for i, row in df.head(20).iterrows():
        # Conta quantas colunas têm texto útil na linha
        preenchidas = sum(1 for v in row.astype(str) if len(v.strip()) > 0 and v.strip().lower() != 'nan')
        linha_texto = " ".join(row.astype(str)).lower()
        
        # O pulo do gato: o cabeçalho real deve ter no mínimo 2 colunas preenchidas
        # Isso impede que a macro-célula de título seja reconhecida como cabeçalho
        if preenchidas >= 2:
            if any(k in linha_texto for k in ["cnpj", "razão", "razao", "empresa", "marca", "dominio", "domínio"]):
                header_idx = i
                break
                
    df.columns = df.iloc[header_idx]
    df = df.iloc[header_idx + 1:].reset_index(drop=True)
    
    # Limpa e desambigua as colunas
    colunas_limpas = []
    for c in df.columns:
        c_str = str(c).replace('\n', ' ').replace('\r', '').strip()
        if not c_str or c_str.lower() == 'nan':
            c_str = "coluna_vazia"
        colunas_limpas.append(c_str)
        
    vistas = {}
    colunas_finais = []
    for c in colunas_limpas:
        if c in vistas:
            vistas[c] += 1
            colunas_finais.append(f"{c}_{vistas[c]}")
        else:
            vistas[c] = 0
            colunas_finais.append(c)
            
    df.columns = colunas_finais
    return df, info

def mapear_colunas(df: pd.DataFrame) -> dict[str, str]:
    """Associa colunas lógicas às colunas reais por palavras-chave (robusto a pequenas mudanças)."""
    regras = {
        "cnpj": lambda h: "cnpj" in h,
        "empresa": lambda h: h.startswith("empresa") or "razao" in h or "denominacao" in h,
        "marcas": lambda h: "marca" in h,
        "dominios": lambda h: "dominio" in h,
        "portaria": lambda h: "portaria" in h,
        "requerimento": lambda h: "requerimento" in h or "sigap" in h,
    }
    usadas: set[str] = set()
    mapa: dict[str, str] = {}
    for logica, teste in regras.items():
        for col in df.columns:
            if col not in usadas and teste(chave_cabecalho(col)):
                mapa[logica] = col
                usadas.add(col)
                break
                
    if "cnpj" not in mapa:
        melhor, taxa = None, 0.0
        # Usa enumerate e iloc para garantir a extração de uma Series (1D), blindando contra colunas de mesmo nome
        for i, col in enumerate(df.columns):
            t = df.iloc[:, i].map(lambda v: len(cnpj14(str(v))) == 14).mean()
            if t > taxa:
                melhor, taxa = col, t
        if melhor is not None and taxa > 0.5:
            mapa["cnpj"] = melhor
        else:
            log(f"AVISO: Não encontrei a coluna de CNPJ explicitamente. Colunas: {list(df.columns)}")
            
    return mapa


def chave_cabecalho(h: str) -> str:
    h = unicodedata.normalize("NFKD", str(h)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", h.lower()).strip()


# ----------------------------------------------------------------------------- tratamento ----
def separa_marcas(txt: str) -> list[str]:
    if not isinstance(txt, str) or not txt.strip():
        return []
    partes = [p.strip(" \t-") for p in re.split(r"[\n\r•;|]+", txt)]
    partes = [p for p in partes if p]
    if len(partes) == 1 and re.search(r"\s{3,}", partes[0]):  # bullets perdidos: 'A   B   C'
        partes = [p.strip() for p in re.split(r"\s{2,}", partes[0]) if p.strip()]
    return partes


def separa_dominios(txt: str) -> list[str]:
    if not isinstance(txt, str):
        return []
    return re.findall(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.bet\.br", txt.lower())


def tratar(df: pd.DataFrame, mapa: dict[str, str]) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for logica in ("empresa", "cnpj", "marcas", "dominios", "portaria", "requerimento"):
        out[logica] = df[mapa[logica]].astype(str).str.strip() if logica in mapa else ""
    out["cnpj"] = out["cnpj"].map(cnpj14)

    # linhas de continuação
    cont = (out["cnpj"] == "") & (out["empresa"] == "") & ((out["marcas"] != "") | (out["dominios"] != ""))
    if cont.any():
        log(f"AVISO: {int(cont.sum())} linha(s) de continuação; herdando CNPJ/empresa da linha anterior.")
        for col in ("cnpj", "empresa", "portaria", "requerimento"):
            out.loc[cont, col] = pd.NA
        out[["cnpj", "empresa", "portaria", "requerimento"]] = out[["cnpj", "empresa", "portaria", "requerimento"]].ffill()

    vazias = (out["cnpj"] == "") | out["cnpj"].isna()
    if vazias.any():
        log(f"AVISO: descartando {int(vazias.sum())} linha(s) sem CNPJ válido (totais/rodapés?).")
        out = out[~vazias].copy()

    out["cnpj_basico"] = out["cnpj"].map(raiz_cnpj)
    out["cnpj_dv_ok"] = out["cnpj"].map(cnpj_valido)
    out["marcas_lista"] = out["marcas"].map(separa_marcas)
    out["dominios_lista"] = out["dominios"].map(separa_dominios)
    out["empresa"] = out["empresa"].str.replace(r"\s+", " ", regex=True).str.strip()
    return out.reset_index(drop=True)


def tabelas_derivadas(aut: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    linhas = []
    for _, r in aut.iterrows():
        marcas, doms = r["marcas_lista"], r["dominios_lista"]
        pareado = len(marcas) == len(doms)
        for i, m in enumerate(marcas):
            linhas.append({
                "cnpj": r["cnpj"], "empresa": r["empresa"], "marca": m,
                "dominio": doms[i] if pareado else "", "portaria": r["portaria"],
                "requerimento": r["requerimento"], "dominio_pareado": pareado,
            })
    marcas_df = pd.DataFrame(linhas, columns=[
        "cnpj", "empresa", "marca", "dominio", "portaria", "requerimento", "dominio_pareado"])

    emp = (
        aut.groupby("cnpj", sort=False)
        .agg(cnpj_basico=("cnpj_basico", "first"), empresa=("empresa", "first"),
             n_autorizacoes=("cnpj", "size"), cnpj_dv_ok=("cnpj_dv_ok", "all"),
             portarias=("portaria", lambda x: " || ".join(dict.fromkeys(v for v in x if v))))
        .reset_index()
    )
    n_marcas = marcas_df.groupby("cnpj")["marca"].nunique().rename("n_marcas")
    lista_marcas = marcas_df.groupby("cnpj")["marca"].agg(lambda x: " | ".join(dict.fromkeys(x))).rename("marcas")
    emp = emp.merge(n_marcas, on="cnpj", how="left").merge(lista_marcas, on="cnpj", how="left")
    emp["n_marcas"] = emp["n_marcas"].fillna(0).astype(int)
    return emp, marcas_df


# ----------------------------------------------------------------------------- main -----------
def main() -> None:
    DIR_SPA.mkdir(parents=True, exist_ok=True)
    meta: dict = {"executado_em": datetime.now().isoformat(timespec="seconds")}

    # Caminhos para os arquivos na pasta "data"
    dir_data = Path("dados")
    arquivo_aut = dir_data / "planilha-de-autorizacoes-1.csv"
    arquivo_proc = dir_data / "ProcessosjudiciaisSPA04.02.26.csv"

    # 1. PROCESSAR AUTORIZAÇÕES
    if not arquivo_aut.exists():
        erro(f"Arquivo não encontrado: {arquivo_aut}")
    
    dados_aut = arquivo_aut.read_bytes()
    meta["origem_autorizacoes"] = str(arquivo_aut)
    
    df_aut, info_aut = ler_tabela(arquivo_aut, dados_aut)
    meta.update({"info_autorizacoes": info_aut})
    
    mapa_aut = mapear_colunas(df_aut)
    log(f"Mapeamento Autorizações: {mapa_aut}")
    
    aut = tratar(df_aut, mapa_aut)
    emp, marcas = tabelas_derivadas(aut)

    # 2. PROCESSAR PROCESSOS JUDICIAIS E INTEGRAR
    if arquivo_proc.exists():
        log(f"Lendo processos judiciais de {arquivo_proc}...")
        dados_proc = arquivo_proc.read_bytes()
        df_proc, _ = ler_tabela(arquivo_proc, dados_proc)
        
        # Tenta mapear o CNPJ no arquivo de processos usando a mesma lógica inteligente
        mapa_proc = mapear_colunas(df_proc)
        if "cnpj" in mapa_proc:
            # Padroniza a coluna de CNPJ na base de processos para fazer o merge corretamente
            df_proc["cnpj_padronizado"] = df_proc[mapa_proc["cnpj"]].astype(str).map(cnpj14)
            
            # Integra (merge) a tabela de empresas autorizadas com os processos judiciais
            # Usando "left" join para manter todas as empresas, com e sem processos
            emp_integrada = emp.merge(
                df_proc, 
                left_on="cnpj", 
                right_on="cnpj_padronizado", 
                how="left",
                suffixes=("", "_processo")
            )
            # Remove a coluna de junção duplicada
            emp_integrada = emp_integrada.drop(columns=["cnpj_padronizado"])
            log("Integração com os processos judiciais concluída com sucesso.")
            emp = emp_integrada
        else:
            log("AVISO: Não foi possível identificar uma coluna de CNPJ no arquivo de processos judiciais para integração.")
    else:
        log(f"AVISO: Arquivo de processos judiciais não encontrado em {arquivo_proc}. Seguindo apenas com as autorizações.")

    # 3. EXPORTAR OS RESULTADOS
    aut.drop(columns=["marcas_lista", "dominios_lista"]).to_csv(DIR_SPA / "autorizacoes_spa.csv", index=False, encoding="utf-8-sig")
    emp.to_csv(DIR_SPA / "bets_empresas_integrada.csv", index=False, encoding="utf-8-sig")
    marcas.to_csv(DIR_SPA / "bets_marcas.csv", index=False, encoding="utf-8-sig")

    meta["n_autorizacoes"] = int(len(aut))
    meta["n_cnpjs_distintos"] = int(emp["cnpj"].nunique())
    meta["n_raizes_distintas"] = int(emp["cnpj_basico"].nunique())
    meta["n_marcas"] = int(len(marcas))
    (DIR_SPA / "meta_spa.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4. RELATÓRIO
    print("\n=== RELATÓRIO ===")
    print(f"Autorizações (linhas):   {meta['n_autorizacoes']}")
    print(f"CNPJs distintos:         {meta['n_cnpjs_distintos']}")
    print(f"Raízes de CNPJ distintas:{meta['n_raizes_distintas']}")
    print(f"Marcas:                  {meta['n_marcas']}")
    
    dup = emp[emp["n_autorizacoes"] > 1]
    if len(dup):
        print("\nEmpresas com mais de uma autorização:")
        print(dup[["empresa", "cnpj", "n_autorizacoes"]].drop_duplicates().to_string(index=False))
        
    ruins = emp[~emp["cnpj_dv_ok"]]
    if len(ruins):
        print("\nATENÇÃO - CNPJs com dígito verificador inválido:")
        print(ruins[["empresa", "cnpj"]].drop_duplicates().to_string(index=False))
        
    print(f"\nArquivos gerados e salvos em: {DIR_SPA}")


if __name__ == "__main__":
    main()