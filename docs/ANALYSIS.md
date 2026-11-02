# Análise estática do instalador `defraggler-2-22-33-995.exe`

Análise feita com o REA (`rea-agents` 6.8.0), 7-Zip, `strings`/`objdump` e decompilação parcial com angr.
Nada foi executado. Resumo do que importa para este projeto.

## Arquivo
- SHA-256 `167b7192937b39e657def16ffb0fdbbab326f007747505d5c8785811d6b03ab8`, 7.466.056 bytes
- PE32 x86, instalador NSIS Unicode (LZMA sólido), `requireAdministrator`, entropia 7,998
- Authenticode lido (Piriform Software Ltd, cadeia DigiCert); a validade da assinatura **não** foi verificada
- Sem exports, TLS callbacks nem load-config; só 7 DLLs importadas (stub NSIS padrão)

## Conteúdo (147 arquivos, ~30,9 MB)
- Produto: `Defraggler.exe`/`Defraggler64.exe`, `df.exe`/`df64.exe` (CLI), extensão de shell, 40 DLLs de idioma
- Módulos de ofertas: `gcapi_dll.dll` (oferta do Chrome), `pfAP.dll` (motor de ofertas), `pfWWW.dll`, `pfUI.dll`
  (12 MB, compartilha código com o CCleaner; contém URLs da Avast)

## Funções observadas no produto (base da lista de funcionalidades deste projeto)
- Análise e desfragmentação via `DeviceIoControl`; modos completo, rápido e de espaço livre
- Desfragmentar pasta/arquivo; mover arquivos de certos tipos/tamanhos para o fim do disco
- Tratamento de SSD; condições de execução (bateria, processos em execução); desligar ao terminar
- Agendamento (mutex `Global\DefragglerTS`); CLI (`/analyze`, `/quick`, `/freespace`, `/recurse`, `/shutdown`…)

## Comportamento do instalador (não replicado aqui)
- Oferece o Google Chrome via `gcapi`, pode definir navegador padrão, e baixa/executa `.exe`/`.msi` escolhido
  por servidor remoto (`pfAP::IsExeOrMSI` lê o cabeçalho: `MZ` ou assinatura OLE `D0CF11E0A1B11AE1` do MSI)
- Contatos de rede: `ccleaner.com`, `piriform.com`, `shepherd.ff.avast.com`, `service.piriform.com`
- A string `brain.jumpshot.com` aparece em `pfUI.dll`; não foi confirmado se o caminho de código é usado

## Limites da análise
Sem Ghidra/Hopper/IDA; só 5 funções do `pfAP.dll` foram decompiladas (com angr). `Defraggler.exe`, `df.exe` e
`pfUI.dll` não foram decompilados; as conclusões sobre eles vêm de imports, strings e recursos.
