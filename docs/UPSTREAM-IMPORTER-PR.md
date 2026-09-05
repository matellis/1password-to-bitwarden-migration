# Upstream PR Scope: Fix 1pux Vault Structure Import

**Target repo**: bitwarden/clients
**Target issue**: [#20724](https://github.com/bitwarden/clients/issues/20724)
**Authored**: 2026-09-05

## Problem Statement

The `OnePassword1PuxImporter` discards all 1Password vault structure on import. Every item from every vault lands in a flat list with no folder or collection assignment. The bug site is a literal TODO comment at line 60 of the importer:

```
// TODO Add handling of multiple vaults
// const personalVaults = account.vaults[0].filter((v) => v.attrs.type === VaultAttributeTypeEnum.Personal);
```

The dead code beneath it references `VaultAttributeTypeEnum` — an enum that was never defined anywhere in the codebase. The comment was a stub that never shipped.

## Upstream File Map

| File | Role |
|---|---|
| `libs/importer/src/importers/onepassword/onepassword-1pux-importer.ts` | Main importer class; contains the TODO at line 60 |
| `libs/importer/src/importers/onepassword/onepassword-1pux-importer.spec.ts` | Jest test suite |
| `libs/importer/src/importers/onepassword/types/onepassword-1pux-importer-types.ts` | TypeScript interfaces for the 1pux JSON schema; `VaultAttributes.type` is `string` with no enum |
| `libs/importer/src/importers/base-importer.ts` | `processFolder()` (line 356), `moveFoldersToCollections()` (line 279), `organization` getter (line 145) |
| `libs/importer/src/models/import-result.ts` | `ImportResult` with `folders`, `folderRelationships`, `collections`, `collectionRelationships` |
| `libs/importer/src/importers/spec-data/onepassword-1pux/sanitized-export.ts` | Multi-item fixture used by folder/collection tests |

The TODO is at:
```
libs/importer/src/importers/onepassword/onepassword-1pux-importer.ts:60
```

## Architecture

**Importer base pattern**

`BaseImporter.processFolder(result, folderName)` creates a `FolderView` if one with that name doesn't exist, then appends a `[cipherIndex, folderIndex]` pair to `result.folderRelationships`. The cipher index is `result.ciphers.length` at call time — meaning `processFolder` must be called before `result.ciphers.push(cipher)`.

`BaseImporter.moveFoldersToCollections(result)` is called at the end of `parse()` when `this.organization` is true. It promotes every folder to a `CollectionView` and every `folderRelationship` to a `collectionRelationship`. No other code path is needed for the org case: fixing the personal-import folder logic automatically fixes org collections.

**Current 1pux importer behavior**

- Iterates `account.vaults` and then `vault.items` — vault name is never used.
- The only folder assignment is in `processOverview`: the first item tag becomes a folder name. Multi-vault, multi-tag data lands in a flat tag-named list with no vault grouping.
- `VaultAttributeTypeEnum` referenced in the TODO comment does not exist in the codebase.

**Vault type values in the 1pux schema**

From [1Password's published format documentation](https://support.1password.com/1pux-format/):

| Value | Meaning |
|---|---|
| `"P"` | Personal vault (called Private in the interface) |
| `"E"` | The Everyone vault (called Shared in the interface) — a real vault holding real items |
| `"U"` | User-created vault |

**Do not filter by vault type.** All three types are real vaults containing real items. `"E"` in particular is the team-wide Shared vault, not an aggregate view — skipping it would silently drop every shared item. `"P"` is the user's own Private vault, which is exactly what an individual wants when importing into their personal Bitwarden vault. The correct behavior is to give every vault a folder, unconditionally.

## Proposed Change

### Personal import: 1pux vault → Bitwarden folder

For each vault, regardless of `attrs.type`, use the vault's `attrs.name` as the folder name. Pass the vault name into `processOverview` so tag-based sub-folders nest naturally: a tagged item in vault "Personal" with tag "Finance" gets folder `"Personal/Finance"`. An untagged item in that vault gets folder `"Personal"`. `processFolder` already normalises `/` as a nesting separator.

### Organization import: 1pux vault → Bitwarden collection

No additional code needed. The `moveFoldersToCollections` call at the end of `parse()` converts every vault folder to a collection and promotes all folder relationships to collection relationships. The vault-as-folder fix feeds directly into the existing org path.

### Steps

**1. Pass vault name through in `parse()`**

```typescript
account.vaults.forEach((vault: VaultsEntity) => {
  const vaultFolderName = vault.attrs.name;
  vault.items.forEach((item: Item) => {
    // ... existing try/catch item processing ...
    this.processOverview(item.overview, cipher, vaultFolderName);
    // ... rest unchanged ...
  });
});
```

**2. Update `processOverview` to accept the vault name**

```typescript
private processOverview(overview: Overview, cipher: CipherView, vaultFolderName?: string) {
  if (overview == null) {
    return;
  }

  cipher.name = this.getValueOrDefault(overview.title);

  if (overview.urls != null) {
    const urls: string[] = [];
    overview.urls.forEach((url: UrlsEntity) => {
      if (!this.isNullOrWhitespace(url.url)) {
        urls.push(url.url);
      }
    });
    cipher.login.uris = this.makeUriArray(urls);
  }

  if (!this.isNullOrWhitespace(vaultFolderName)) {
    const tag =
      overview.tags != null && overview.tags.length > 0
        ? this.capitalize(overview.tags[0])
        : null;
    const folderName = tag ? `${vaultFolderName}/${tag}` : vaultFolderName;
    this.processFolder(this.result, folderName);
  } else if (overview.tags != null && overview.tags.length > 0) {
    this.processFolder(this.result, this.capitalize(overview.tags[0]));
  }
}
```

The `else` branch preserves backward compatibility for any call site that doesn't pass a vault name (there are none in production, but this keeps the spec tests for the flat tag behavior passing without modification).

## Mapping to Our Conversion Tables

Our working reference implementation in `lib/onepux.py` uses `make_collection(org_id, vault_name)` for the org path and a per-vault file split for personal imports. The field and category mappings are identical to what the upstream importer already does. Field-level mapping details are in our [MAPPING.md](https://github.com/matellis/1password-to-bitwarden-migration/blob/main/docs/MAPPING.md). That document covers category UUIDs, login fields, section field types, credit card fields, identity fields, and SSH key fields — all of which are handled by the existing upstream code and are outside the scope of this PR.

## Test Plan

The importer tests live in:
```
libs/importer/src/importers/onepassword/onepassword-1pux-importer.spec.ts
```

Tests run with `nx test importer` or `npx jest --testPathPattern=onepassword-1pux` from the repo root.

New test cases to add inside the existing `describe("1Password 1Pux Importer")` block:

**Vault → folder (personal import), all vault types**
```typescript
it("should create one folder per vault, for every vault type", async () => {
  const threeVaultData = {
    accounts: [{
      vaults: [
        {
          attrs: { uuid: "v1", name: "Private", type: "P", desc: "", avatar: "" },
          items: [/* one login item with no tags */],
        },
        {
          attrs: { uuid: "v2", name: "Shared", type: "E", desc: "", avatar: "" },
          items: [/* one login item with no tags */],
        },
        {
          attrs: { uuid: "v3", name: "Work", type: "U", desc: "", avatar: "" },
          items: [/* one login item with no tags */],
        },
      ],
    }],
  };
  const importer = new OnePassword1PuxImporter(configService);
  const result = await importer.parse(JSON.stringify(threeVaultData));

  expect(result.ciphers.length).toBe(3);
  expect(result.folders.map((f) => f.name)).toEqual(["Private", "Shared", "Work"]);
  expect(result.folderRelationships.length).toBe(3);
});
```

**Vault → collection (org import)**
```typescript
it("should create one collection per vault when importing to an org", async () => {
  // same threeVaultData fixture as above
  const importer = new OnePassword1PuxImporter(configService);
  importer.organizationId = Utils.newGuid() as OrganizationId;
  const result = await importer.parse(JSON.stringify(threeVaultData));

  expect(result.ciphers.length).toBe(3);
  expect(result.collections.map((c) => c.name)).toEqual(["Private", "Shared", "Work"]);
  expect(result.folders.length).toBe(0);          // moveFoldersToCollections clears this
});
```

**Tagged item within a vault gets nested folder**
```typescript
it("should nest tag under vault name as folder", async () => {
  // item with tags: ["Finance"] inside vault named "Personal"
  const result = await importer.parse(/* ... */);
  expect(result.folders[0].name).toBe("Personal/Finance");
});
```

**Existing tests to update**

The existing "should create folders" and "should create collections if part of an organization" tests use the `SanitizedExport` fixture, which has one vault named `"T's Test Vault"` and items tagged with five different tags. After the fix, those tests will see five folders named `"T's Test Vault/Movies"`, `"T's Test Vault/Finance"`, etc. rather than just the bare tag names. Update the `expect(folders[0].name)` assertions accordingly, or add a second fixture with the old names and keep both.

## Draft PR Description

**Title**: fix(importer): preserve 1pux vault structure as folders/collections on import

Closes #20724.

**Problem**

The 1pux importer discards all vault structure. Every item from every vault lands in a flat, unorganised list. A TODO comment in the parser marks where multi-vault handling was intended but never completed.

**Change**

- Pass each vault's `attrs.name` into `processOverview` as the folder name, for every vault regardless of type (Personal, Everyone/Shared, and user-created vaults all hold real items).
- Items with tags get a nested folder `"VaultName/TagName"`; untagged items get `"VaultName"`.
- The org import path is unchanged at the call site — `moveFoldersToCollections` already promotes vault folders to collections.

**Testing**

- New unit tests: three-vault personal import across all vault types, org import collection mapping, tagged-item nesting.
- Existing folder/collection tests updated: `SanitizedExport` fixture items now land in `"T's Test Vault/Movies"` etc. instead of `"Movies"`.

## Honest Risks

**Review latency**

The bitwarden/clients monorepo moves fast and has a large reviewer pool but also a large PR queue. Issues tagged "good first issue" or touching importers can sit for weeks. Expect at least one round of naming or UX feedback before merge.

**UX choice: nested vs flat**

Using `"VaultName/TagName"` creates nested folders in Bitwarden. Some reviewers may prefer flat `"TagName"` folders (the current behavior) and a separate flat `"VaultName"` folder. The nested approach is technically correct — it reflects the original structure — but the team may want a flag or a simpler flat approach. Be prepared to defend or pivot.

**Behavior change for existing tag-folders users**

Users who previously imported a 1pux and got tag-named folders will now get vault-named top-level folders with tags nested underneath. That is the point of the fix, but it is a visible behavior change worth calling out in release notes.

**Multi-account exports**

The parser only processes `exportData.accounts[0]`. Multi-account exports would still silently drop accounts 1+. This PR does not change that and should not try to — it's a separate scope.

**Release cadence**

Bitwarden clients ship on a roughly two-week release cycle. Even after merge, vault folder support will only appear to end users after the next web/desktop release.

**Our split.py**

Once this lands and ships, `split.py` in our repo becomes redundant for the personal-import folder case. The org-import path in our toolkit (which creates Bitwarden collections via the API) remains useful for bulk migrations that bypass the importer UI entirely.
