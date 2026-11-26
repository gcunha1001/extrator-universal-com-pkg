# Avisos de terceiros

Este projeto é uma adaptação de código **GPL-3.0-only** do projeto
[ps4-pkg-extractor-ui](https://github.com/murums04/ps4-pkg-extractor-ui).

**Nenhum binário de terceiros é redistribuído neste repositório.** Os textos das
licenças aplicáveis estão em [`licenses/`](licenses/). O motor PKG é uma
dependência externa: o usuário deve instalá-lo por conta própria, a partir do
release upstream; o pacote de código-fonte correspondente
(`vendor/shadps4plus` com `SOURCE.json`/ZIP) não acompanha esta instalação.

## Interface e adaptações

- **ps4-pkg-extractor-ui** — Copyright 2026 murums.
- Licença: **GPL-3.0-only**.
- URL: https://github.com/murums04/ps4-pkg-extractor-ui
- Texto integral da licença: [`LICENSE`](LICENSE).

## Motor de extração PKG

- **pkg_extractor**, de **AzaharPlus/shadPS4Plus**, derivado do projeto
  shadPS4 Emulator Project.
- Release: `PKG_EXTRACTOR_1_1`; commit
  `771a8d58aff540448c7cbace7c40a66a5daf010d`.
- Licença: **GPL-2.0-or-later**, incluindo os avisos de copyright
  individuais dos arquivos upstream.
- URL: https://github.com/AzaharPlus/shadPS4Plus
- Texto da licença: [`licenses/GPL-2.0.txt`](licenses/GPL-2.0.txt).
- O executável **não** está incluído aqui. Instale-o separadamente pelo projeto
  [ps4-pkg-extractor-ui](https://github.com/murums04/ps4-pkg-extractor-ui) e configure
  `EXTRATOR_PKG_DIR` com o diretório que contém `pkg_extractor`.

## Arrastar e soltar

- **tkinterdnd2** 0.7.0 — Copyright (c) 2020 Philippe Gagné.
- Licença: **MIT**.
- URL: https://github.com/Eliav2/tkinterdnd2
- Texto da licença: [`licenses/tkinterdnd2-MIT.txt`](licenses/tkinterdnd2-MIT.txt).

## tkdnd (Tcl)

- Componente Tcl/Tk usado pelo `tkinterdnd2` para arrastar e soltar.
- Copyright: George Petasis.
- Licença: **permissiva**, com termos disponíveis na própria distribuição do
  `tkdnd`/`tkinterdnd2` instalada pelo usuário.
- Nenhum binário do `tkdnd` é redistribuído neste repositório.
