# tinyman-amm-contracts-v2
Tinyman AMM Contracts V2

Tinyman is an automated market maker (AMM) implementation on Algorand.

### Docs

The protocol is described in detail in the following document:
[Tinyman AMM V2 Protocol Specification](docs/Tinyman%20AMM%20V2%20Protocol%20Specification.pdf)


### Contracts
The contracts are written in [Tealish](https://github.com/tinymanorg/tealish).
The specific version of Tealish is https://github.com/tinymanorg/tealish/tree/0cec751154b0083c2cb79da43b40aa26b367ecc4.

The annotated TEAL outputs and compiled bytecode are available in the [build](contracts/build/) folder.

The Tealish source can be compiled as follows:
```
    tealish contracts/
```
The `.teal` files will be output to the `contracts/build` directory.

A VS Code extension for syntax highlighting of Tealish & TEAL is available [here](https://www.dropbox.com/s/zn3swrfxkyyelpi/tealish-0.0.1.vsix?dl=0)


### Tests
Tests are included in the `tests/` directory. [AlgoJig](https://github.com/Hipo/algojig) and [Tealish](https://github.com/tinymanorg/tealish) are required to run the tests.

Set up a new virtualenv and install the specific versions of AlgoJig & Tealish & AlgoSDK with `pip install -r requirements.txt`.

```
    python -m unittest
```


### Bug Bounty Program
Details to be announced in the week of the 28th November.

Reports of potential flaws must be responsibly disclosed to `security@tinyman.org`. Do not share details with anyone else until notified to do so by the team.

### Audit
An audit of these contracts has been completed by [Runtime Verification](https://runtimeverification.com/). It can be found in [their GitHub repo](https://github.com/runtimeverification/publications/tree/main/reports/smart-contracts/Tinyman-amm-v2-audit).


### Acknowledgements
The Tinyman team would like to thank Runtime Verification for their insightful comments and code improvement suggestions.


### Licensing

The contents of this repository are licensed under the Business Source License 1.1 (BUSL-1.1), see [LICENSE](LICENSE).
