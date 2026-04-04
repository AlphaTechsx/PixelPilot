for (const specifier of ['electron/main','electron/renderer']) {
  try {
    const mod = await import(specifier);
    console.log(specifier, 'module-keys', Object.keys(mod));
    console.log(specifier, 'default-type', typeof mod.default);
    if (mod.default && typeof mod.default === 'object') {
      console.log(specifier, 'default-keys', Object.keys(mod.default).slice(0,30));
    } else {
      console.log(specifier, 'default-value', mod.default);
    }
  } catch (error) {
    console.error(specifier, 'error', error);
  }
}
setTimeout(() => process.exit(0), 1000);
