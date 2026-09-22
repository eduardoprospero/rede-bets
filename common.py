"""Utilitários compartilhados pelos scripts do projeto (mercado de bets - rede de propriedade).

Convenção de pastas (raiz configurável pela variável de ambiente BETS_DADOS; padrão: ./dados):

    dados/spa/              lista oficial da SPA/MF (bruta + tabelas normalizadas)
    dados/receita_zip/AAAA-MM/      zips baixados da Receita Federal
    dados/receita_parquet/AAAA-MM/  tabelas convertidas (parquet)
    dados/bets/AAAA-MM/     base final das bets (validação + sócios de 1º nível)
"""
from __future__ import annotations

import os
import re
import sys
import time
import unicodedata
from pathlib import Path

DATA_DIR = Path(os.environ.get("BETS_DADOS", "dados")).resolve()
DIR_SPA = DATA_DIR / "spa"
DIR_ZIP = DATA_DIR / "receita_zip"
DIR_PARQUET = DATA_DIR / "receita_parquet"
DIR_BETS = DATA_DIR / "bets"

# --- Receita Federal -------------------------------------------------------------------------
# Após a mudança de layout de jan/2026, os projetos open-source rictom/cnpj-sqlite e
# rictom/rede-cnpj listam e baixam os arquivos por WebDAV (Nextcloud) neste host, usando um
# "share token" público. ESSE TOKEN PODE MUDAR: se o script 02 falhar, abra
# https://arquivos.receitafederal.gov.br/ no navegador, entre em Dados > Cadastros > CNPJ e copie
# o código que aparece na URL depois de /index.php/s/ ; passe-o com --token.
RFB_HOST = "https://arquivos.receitafederal.gov.br"
RFB_SHARE_TOKEN = "YggdBLfdninEJX9"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

# --- Layouts (dicionário oficial: https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf) --
# Os CSVs da Receita NÃO têm cabeçalho; a ordem abaixo segue o dicionário de dados.
COLUNAS_EMPRESAS = [
    "cnpj_basico", "razao_social", "natureza_juridica", "qualificacao_responsavel",
    "capital_social", "porte", "ente_federativo",
]
COLUNAS_ESTABELECIMENTOS = [
    "cnpj_basico", "cnpj_ordem", "cnpj_dv", "matriz_filial", "nome_fantasia",
    "situacao_cadastral", "data_situacao_cadastral", "motivo_situacao_cadastral",
    "cidade_exterior", "pais", "data_inicio_atividade", "cnae_fiscal", "cnae_secundaria",
    "tipo_logradouro", "logradouro", "numero", "complemento", "bairro", "cep", "uf",
    "municipio", "ddd1", "telefone1", "ddd2", "telefone2", "ddd_fax", "fax",
    "correio_eletronico", "situacao_especial", "data_situacao_especial",
]
COLUNAS_SOCIOS = [
    "cnpj_basico", "identificador_socio", "nome_socio", "doc_socio", "qualificacao_socio",
    "data_entrada", "pais", "representante_legal", "nome_representante",
    "qualificacao_representante", "faixa_etaria",
]

SITUACAO_CADASTRAL = {1: "NULA", 2: "ATIVA", 3: "SUSPENSA", 4: "INAPTA", 8: "BAIXADA"}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def erro(msg: str, codigo: int = 1) -> None:
    print(f"ERRO: {msg}", file=sys.stderr, flush=True)
    sys.exit(codigo)


# --- Identificadores -------------------------------------------------------------------------
# Desde julho/2026 o CNPJ pode ter LETRAS (IN RFB 2.229/2024). Por isso NÃO se usa "só dígitos"
# para CNPJ: mantêm-se letras e números, em maiúsculas. Para CPF (sempre numérico) usa-se
# so_digitos().

def normaliza_id(valor) -> str:
    """Remove pontuação e coloca em maiúsculas, preservando letras (CNPJ alfanumérico)."""
    if not isinstance(valor, str):
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", valor).upper()


def so_digitos(valor) -> str:
    if not isinstance(valor, str):
        return ""
    return re.sub(r"\D", "", valor)


def cnpj14(valor) -> str:
    """CNPJ completo com 14 caracteres ('' se não for possível). Repõe zeros à esquerda se a
    fonte (ex.: Excel) os tiver removido de um CNPJ puramente numérico."""
    s = normaliza_id(valor)
    if s.isdigit() and 0 < len(s) < 14:
        s = s.zfill(14)
    return s if len(s) == 14 else ""


def raiz_cnpj(valor) -> str:
    """Raiz (8 primeiros caracteres). Aceita CNPJ completo (14) ou só a raiz (8) — a partir de
    ago/2026 a coluna de sócio PJ da Receita passou a trazer só a raiz, segundo o código do
    projeto cnpj-sqlite (não inspecionei os dados reais)."""
    s = normaliza_id(valor)
    return s[:8] if len(s) in (8, 14) else ""


_PESOS = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]


def _dv(seq: str) -> int:
    pesos = _PESOS[-len(seq):]
    soma = sum((ord(c) - 48) * p for c, p in zip(seq, pesos))
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def dv_cnpj(base12: str) -> str:
    """Dígitos verificadores de uma base de 12 caracteres (numérica ou alfanumérica).
    Módulo 11, valor do caractere = código ASCII - 48 (regra oficial do CNPJ alfanumérico)."""
    d1 = _dv(base12)
    d2 = _dv(base12 + str(d1))
    return f"{d1}{d2}"


def cnpj_valido(c14: str) -> bool:
    return len(c14) == 14 and dv_cnpj(c14[:12]) == c14[12:]


def normaliza_nome(valor) -> str:
    """MAIÚSCULAS, sem acentos, espaços colapsados — para comparar nomes."""
    if not isinstance(valor, str):
        return ""
    s = unicodedata.normalize("NFKD", valor).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s).strip().upper()


def mes_mais_recente(diretorio: Path) -> str:
    """Maior subpasta no formato AAAA-MM dentro de `diretorio`."""
    meses = sorted(p.name for p in diretorio.glob("*") if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}", p.name))
    if not meses:
        erro(f"Nenhuma subpasta AAAA-MM encontrada em {diretorio}. Rode os scripts anteriores ou use --mes.")
    return meses[-1]
