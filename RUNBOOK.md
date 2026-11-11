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
.\scripts\executar-live.ps1 -Tribunal trf6   # cadastro real + pedido do link de senha
.\scripts\criar-senha.ps1 -Tribunal trf6     # link do e-mail -> senha -> testa CPF + senha + 2FA
.\scripts\relatorio.ps1                      # melhor resultado por tribunal e situacao da meta
```

### Credencial utilizavel (CPF + senha + 2FA)

So o cadastro com certificado nao basta: sem senha o PJe nao libera o acesso por CPF
(ou trata o usuario como jus postulandi). Por isso, depois de cada cadastro:

1. o termo de compromisso precisa estar assinado (o robo assina) e o e-mail correto;
2. o robo sai do PJe, abre a pagina inicial, clica em "Esqueceu a senha?" e informa o CPF
   (e o e-mail, se o tribunal pedir); o PJe manda o link de criacao de senha ao e-mail cadastrado;
3. com o link em maos, `criar-senha.ps1` abre o link, cadastra a senha e testa o login com
   CPF + senha + codigo do 2FA. Se o pedido automatico do link falhar, o mesmo script refaz o pedido.

E-mail errado no cadastro: entre com o certificado, corrija o e-mail nos dados do usuario e
peca o link de novo (`criar-senha.ps1`, respondendo S ao pedido do link).
O relatorio so conta para a meta os tribunais com cadastro real **e** credencial testada.

Meta de validacao: cadastro real (`COMPLETED`) em pelo menos 5 tribunais, com 1 TRF, 1 TRT e 1 TJ.
`ALREADY_REGISTERED` nao conta: escolha tribunais em que o advogado ainda nao tem cadastro.

## 7. Execucao geral (planilha, um advogado por linha)

Um comando faz o fluxo inteiro para cada advogado da planilha, no tribunal escolhido:
cadastro com o A1 -> termo assinado -> "Solicitar nova senha" -> link do e-mail -> senha forte
-> teste de CPF + senha + 2FA -> planilha de entrega protegida (`entregas\credenciais-<data>.xlsx`).

```powershell
.\scripts\executar.ps1 -Modelo     # 1a vez: cria planilhas\advogados.xlsx para preencher
.\scripts\executar.ps1             # pergunta o tribunal, o modo e a planilha
.\scripts\executar.ps1 -Tribunal tjpe -Planilha planilhas\advogados.xlsx
.\scripts\executar.ps1 -Tribunal "tremg,tjpa,trt5"   # varios tribunais, um depois do outro
```

- Varios tribunais: responda "tremg, tjpa, trt5" (ou os numeros "8,3,31"). O robo faz um tribunal de
  cada vez, com todos os advogados em cada um; senhas do certificado, chave 2FA e caixa de e-mail sao
  pedidas uma vez por advogado; no fim ha uma unica planilha de entrega e um resumo geral.
  No modo real, tribunais sem envio liberado ficam de fora (o robo avisa e pede confirmacao).

- Tribunal sem envio real liberado no `.toml` roda so em ENSAIO.
- Colunas obrigatorias: nome, cpf, oab_numero, oab_uf, email, telefone, cep, logradouro, numero,
  bairro, cidade, uf. `autorizacao_ref` e opcional (se preenchida, fica na auditoria).
- Coluna `certificado`: **vazia** = usa o A1 ja instalado no Windows (Cert:\CurrentUser\My) com o
  CPF da linha, sem .pfx e sem senha (havendo renovacao, o de validade mais longa); **thumbprint**
  (40 caracteres) = aquele certificado instalado; **caminho do .pfx** = instala so durante a execucao.
  Antes de comecar, o robo mostra qual certificado vai usar em cada linha.
- Opcionais e sensiveis: senha_certificado (so para .pfx), chave_2fa, imap_senha. Vazias = o robo pergunta na hora.
  Se preencher, proteja a planilha com senha no Excel; a pasta `planilhas\` nao vai para o Git.
- `executar = N` pula a linha. Um advogado com erro nao para o lote.
- Advogado ja cadastrado no tribunal: a senha so e trocada se o operador confirmar no inicio.
- Caixa Gmail sem senha de app: preencha `email_senha` (senha do e-mail) e `email_2fa` (chave de 32
  caracteres do 2FA do Gmail) na planilha. Na primeira vez o robo entra no Gmail pelo Edge, cria a senha
  de app e guarda cifrada pelo Windows em `runtime\caixas-email.json` (so o mesmo usuario da mesma
  maquina decifra). Nas proximas usa IMAP direto. Se o Google pedir captcha/celular, o robo pausa para o
  operador (nada e contornado).
- `-SoSenha`: para quem JA tem cadastro naqueles tribunais. Nao entra com o certificado (evita bloqueio
  por muitos logins seguidos) e vai direto para "Esqueci minha senha" + e-mail + nova senha + teste.


## 8. Formularios diferentes em cada PJe (adaptacao)

O robo identifica cada campo pelo rotulo e pelo bloco da tela, e so preenche o que tem certeza:

- campos de outro assunto com rotulo parecido (UF de nascimento, UF do RG/orgao expedidor, telefone
  comercial...) sao ignorados; "Estado civil" nao e confundido com "Estado/UF";
- empate entre dois campos: vence o que ja tem o valor certo, o que esta no bloco "Endereco" ou o mais
  proximo do CEP; sem evidencia, o robo nao chuta;
- endereco bloqueado e ja preenchido pelo PJe (pelo CEP) e mantido como esta e registrado;
- "E-mail (confirmacao)" recebe o mesmo e-mail; RG, orgao expedidor e data de expedicao vem das colunas
  opcionais `rg_numero`, `rg_orgao` e `rg_data_expedicao` da planilha (os TRTs exigem);
- "Estado civil" vem da coluna opcional `estado_civil` (solteiro(a), casado(a)...; os TRTs exigem). Sem ela,
  o modo assistido pausa para o operador escolher no Edge;
- TRTs: o pedido de nova senha usa o "Esqueci minha senha" da pagina do PJe (antes de "Entrar com PDPJ");
- PJe que so avisa "campo obrigatorio" depois de enviar (TRTs): o robo reavalia o formulario devolvido,
  preenche o que tiver dado e, no modo assistido, pausa para o operador completar o resto no Edge;
- o ensaio avisa quando o formulario tem campo sem dado na planilha (ex.: RG);
- quando para, deixa print em `runtime\audit\<execucao>\tela-*.png` e mostra os rotulos envolvidos.

**Modo assistido** (execucao geral, ligado por padrao): se o robo tiver duvida, ele pausa com o Edge
aberto. Corrija o que faltar no proprio Edge e aperte Enter para ele reavaliar, ou digite C se o
formulario ja estiver certo. Divergencia de identidade (nome, CPF, OAB) nunca passa por esse caminho.

**Campo fixado**: se um tribunal sempre gerar a mesma duvida, fixe o campo no .toml dele, por exemplo
`campo_uf = ["id=formEndereco:uf"]` dentro de `[selectors]` (veja o comentario em `_comum.toml`).
