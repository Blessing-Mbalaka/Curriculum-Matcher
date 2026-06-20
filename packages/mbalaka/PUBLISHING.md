# Publishing Mbalaka to npm

The package is already prepared locally. You only need an npm account and a working connection to the npm registry.

## 1. Create an npm account

Go to:

```text
https://www.npmjs.com/signup
```

Verify your email address before publishing.

## 2. Log in from this project

From this folder:

```bash
cd packages/mbalaka
npm login
```

If PowerShell blocks `npm.ps1`, use:

```bat
cmd /c npm login
```

Check login:

```bash
npm whoami
```

## 3. Check the package name

```bash
npm view mbalaka version
```

If npm returns `404`, the name is available. If it returns a version, choose another name or publish under a scope such as `@yourname/mbalaka`.

For a scoped package, update `package.json`:

```json
{
  "name": "@yourname/mbalaka",
  "publishConfig": {
    "access": "public"
  }
}
```

## 4. Build and inspect

```bash
npm run build
npm pack --dry-run
```

## 5. Publish

```bash
npm publish --access public
```

After publishing, install it in another app with:

```bash
npm install mbalaka three
```

or, for a scoped package:

```bash
npm install @yourname/mbalaka three
```

## Updating Later

Every npm publish needs a new version:

```bash
npm version patch
npm publish --access public
```
