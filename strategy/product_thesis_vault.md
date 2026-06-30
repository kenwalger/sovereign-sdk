# Product Thesis Addendum: Memory → Custody (sovereign-vault)

## The Boundary

| Boundary | Component |
| :--- | :--- |
| Memory → Custody | `sovereign-vault`[cite: 11, 13] |

## The Problem Space

Local-first systems operating on the edge or developer workstations frequently manipulate ephemeral data structures in active memory. However, when that data must transition to durable, long-term custody, it faces immediate vectors of exposure:
* **Cold Boot Attacks & Disk Seizure:** Physical extraction of edge hardware exposes unencrypted local data stores.
* **Cryptographic Drift:** Local data stores signed with static keys become vulnerable over extended operational lifecycles.
* **Contextual Pollution:** Blindly archiving every state transition clogs local storage media with non-actionable data history.

## The Vault Imperative

`sovereign-vault` governs the boundary where volatile in-memory operations freeze into long-term, trusted local storage. 

It does not compete with heavy enterprise key-management systems (like HashiCorp Vault); instead, it acts as a lightweight, zero-dependency local custodian that:
1. Automatically handles hardware-bound, localized cryptographic sealing using hardware security modules (HSM) or Trusted Platform Modules (TPM) found on edge chips.
2. Implements time-locked, zero-knowledge decay states for local logs, ensuring that old data automatically self-destructs or compacts if it does not receive a remote synchronization pulse.
3. Enforces strict cryptographic separation between distinct local operational namespaces.