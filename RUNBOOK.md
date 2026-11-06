# Runbook de homologação e operação

## 1. Definir o escopo

Registre o tribunal, a instância, a URL oficial e se o fluxo é primeiro cadastro ou confirmação
administrativa. Este projeto implementa **cadastro automatico operado pela pagina local**, com o A1 do titular.

## 2. Preparar sem dados reais

Execute:

```powershell
.\scripts\preparar.ps1
.\scripts\testar.ps1 -Python .\.venv\Scripts\python.exe
.\scripts\simular.ps1 -Python .\.venv\Scripts\python.exe
```

Também teste os cenários de parada:

```powershell
.\scripts\simular.ps1 -Scenario captcha -Python .\.venv\Scripts\python.exe
.\scripts\simular.ps1 -Scenario jus_postulandi -Python .\.venv\Scripts\python.exe
.\scripts\simular.ps1 -Scenario second_factor -Python .\.venv\Scripts\python.exe
.\scripts\simular.ps1 -Scenario already_registered -Python .\.venv\Scripts\python.exe
```

## 3. Levantar seletores do tribunal

Faça a inspeção em ambiente autorizado, usando somente a interface normal. Para cada grupo em
`[selectors]`, registre pelo menos um seletor semântico estável. Dê preferência a `role` e `label`;
use CSS apenas quando não houver alternativa. Não use coordenadas de tela.

Confirme especialmente os seletores da pagina do PJe e, alem deles, os nomes das janelas
do PJeOffice PRO (secao `[signer]`), levantados com:

```powershell
.\scripts\inspecionar-assinador.ps1
```

Confirme especialmente:

- botão de acesso por certificado;
- campos retornados de nome, CPF e OAB;
- formulário de contato e endereço;
- botão de confirmação;
- termo de compromisso e botão de assinatura;
- mensagem inequívoca de conclusão;
- campo e botao do segundo fator;
- painel de advogado já cadastrado;
- mensagens de CAPTCHA, bloqueio e erro de certificado;
- janela de selecao de certificado e de confirmacao do PJeOffice.

## 4. Homologar

Copie os arquivos de exemplo para `config.toml` e `cadastro.json`. Preencha a URL oficial e os
seletores. Mantenha inicialmente:

```toml
environment = "simulation"
selectors_approved = false
allow_live_submission = false
```

Depois da revisão por duas pessoas, altere para:

```toml
environment = "production"
selectors_approved = true
allow_live_submission = true
```

O modo real também bloqueia domínios que não terminem em `.jus.br` e dados ainda marcados como
exemplo/teste.

## 4.1 Ensaiar no PJe real

Antes de habilitar o envio, rode o ensaio. Ele exige apenas `environment = "production"` e
dominio `.jus.br`. Faz o login com o A1, confere nome/CPF/OAB, preenche o formulario e
**para antes de "Confirmar dados"**: nada e enviado nem assinado.

```powershell
.\scripts\ensaiar.ps1 -Tribunal tjpa
```

Cada tribunal tem seu arquivo em `tribunais\` (hoje: `tjap.toml`, `tjpa.toml`, `trt2.toml`). Para um novo
tribunal, copie um deles, troque `tribunal`/`base_url` e mapeie o botao do certificado.

Resultado esperado: `REHEARSAL_OK`. Se der `FAILED`, o evento `flow_failed` do `audit.jsonl`
traz em `page` os titulos, rotulos, botoes e nomes de campos da tela em que parou (nome do
titular e numeros mascarados). Use isso para ajustar os seletores e ensaie de novo.

## 5. Executar

Com o PJeOffice PRO aberto e validado:

```powershell
.\scripts\executar-live.ps1
```

Isso abre `http://127.0.0.1:8723/`. Na pagina, solte o `.pfx`, digite a senha, confira os
dados e clique em iniciar. O robo conduz todo o cadastro. A unica intervencao possivel e
digitar o codigo de segundo fator, se o PJe pedir. CAPTCHA ou divergencia encerram a
execucao.

## 6. Encerrar

Leia `runtime/audit/<run_id>/result.json` e verifique `audit.jsonl`:

```powershell
.\.venv\Scripts\python.exe -m pje_rpa verify-audit runtime\audit\<run_id>\audit.jsonl
```

Resultados possíveis:

- `COMPLETED`: tela final de conclusão encontrada;
- `ALREADY_REGISTERED`: painel do advogado já estava disponível;
- `NEEDS_HUMAN`: confirmação humana não concluída;
- `BLOCKED`: CAPTCHA, certificado, acesso ou perfil incorreto;
- `FAILED`: elemento esperado ou requisito técnico não encontrado.


## 6. Expandir para novos tribunais

Todos os arquivos de `tribunais\` herdam de `tribunais\_comum.toml` (`herda = "_comum.toml"`).
Um tribunal novo normalmente precisa so de `[app]` com `tribunal` e `base_url`; sobrescreva em
`[selectors]` apenas o que for diferente naquele PJe.

Para cada tribunal, nesta ordem:

```powershell
.\scripts\sondar.ps1 -Tribunal trf6          # abre so o login: URL e botao de certificado ok?
.\scripts\ensaiar.ps1 -Tribunal trf6         # login real + formulario, para antes de enviar
# conferido o ensaio: selectors_approved = true e allow_live_submission = true no trf6.toml
.\scripts\executar-live.ps1 -Tribunal trf6   # cadastro real
.\scripts\relatorio.ps1                      # melhor resultado por tribunal e situacao da meta
```

Meta de validacao: cadastro real (`COMPLETED`) em pelo menos 5 tribunais, com 1 TRF, 1 TRT e 1 TJ.
`ALREADY_REGISTERED` nao conta: escolha tribunais em que o advogado ainda nao tem cadastro.
