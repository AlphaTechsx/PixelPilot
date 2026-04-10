const fs = require('node:fs');
const path = require('node:path');

const MARKER_START = '<!-- PixelPilot MSI customization start -->';
const MARKER_END = '<!-- PixelPilot MSI customization end -->';
const MAIN_EXECUTABLE_COMPONENT_ID = 'MainExecutableComponent';
const WIX_QUIET_EXEC_BINARY_KEY = 'WixCA';
const WIX_QUIET_EXEC_REF_ID = 'WixQuietExec';

const TASK_NAMES = {
  orchestrator: 'PixelPilot Orchestrator',
  agent: 'PixelPilot UAC Agent',
};

const projectRoot = path.resolve(__dirname, '..');
const licenseFilePath = path.join(projectRoot, 'build', 'license_en.rtf');
const removeUserDataScriptPath = path.join(projectRoot, 'resources', 'installer', 'remove-user-data.ps1');

function xmlAttr(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function replaceOrThrow(source, pattern, replacement, label) {
  if (!pattern.test(source)) {
    throw new Error(`Unable to find ${label} in MSI project.`);
  }
  return source.replace(pattern, replacement);
}

function stripExistingCustomization(source) {
  return source
    .replace(new RegExp(`\\s*${MARKER_START}[\\s\\S]*?${MARKER_END}\\s*`, 'g'), '\n')
    .replace(/\s*<ComponentGroup Id="DesktopShortcutComponents"[\s\S]*?<\/ComponentGroup>\s*/g, '\n')
    .replace(/\s*<ComponentGroup Id="StartupComponents"[\s\S]*?<\/ComponentGroup>\s*/g, '\n');
}

function ensureFileExists(targetPath, label) {
  if (!fs.existsSync(targetPath)) {
    throw new Error(`${label} is missing: ${targetPath}`);
  }
}

function windowsPath(targetPath) {
  return path.resolve(targetPath).replace(/\//g, '\\');
}

function quoteForTask(targetPath) {
  return `\\"${targetPath}\\"`;
}

function taskCreateCommand(taskName, targetPath) {
  return `schtasks /Create /F /SC ONSTART /RU SYSTEM /RL HIGHEST /TN "${taskName}" /TR "${quoteForTask(targetPath)}"`;
}

function taskDeleteCommand(taskName) {
  return `schtasks /Delete /TN "${taskName}" /F >NUL 2>&1`;
}

function buildInstallTasksCommand() {
  const orchestratorPath = '[APPLICATIONFOLDER]resources\\runtime\\orchestrator\\orchestrator.exe';
  const agentPath = '[APPLICATIONFOLDER]resources\\runtime\\agent\\agent.exe';
  return `"[%ComSpec]" /c ${taskCreateCommand(TASK_NAMES.orchestrator, orchestratorPath)} && ${taskCreateCommand(TASK_NAMES.agent, agentPath)}`;
}

function buildRemoveTasksCommand() {
  return `"[%ComSpec]" /c ${taskDeleteCommand(TASK_NAMES.orchestrator)} & ${taskDeleteCommand(TASK_NAMES.agent)} & exit /b 0`;
}

function buildRemoveMachineDataCommand() {
  return '"[%ComSpec]" /c if exist "%ProgramData%\\PixelPilot" rmdir /S /Q "%ProgramData%\\PixelPilot" & exit /b 0';
}

function buildRemoveUserDataCommand(mode) {
  const installedScriptPath = '[APPLICATIONFOLDER]resources\\installer\\remove-user-data.ps1';
  return `"[%ComSpec]" /c powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "${installedScriptPath}" -Mode ${mode}`;
}

function buildFeatureBlock(productName) {
  return [
    `<Feature Id="ProductFeature" Title="${xmlAttr(productName)}" Description="Core application files, startup integration, and Windows background services." Display="expand" Absent="disallow">`,
    '  <ComponentGroupRef Id="ProductComponents" Primary="yes"/>',
    '  <ComponentGroupRef Id="StartupComponents"/>',
    `  <Feature Id="DesktopShortcutFeature" Title="Desktop shortcut" Description="Create a desktop shortcut for ${xmlAttr(productName)}." Level="2" AllowAdvertise="no">`,
    `    <ComponentRef Id="${MAIN_EXECUTABLE_COMPONENT_ID}"/>`,
    '    <ComponentGroupRef Id="DesktopShortcutComponents"/>',
    '  </Feature>',
    '</Feature>',
  ].join('\n    ');
}

function buildShortcutComponentGroup(productName, iconId) {
  const iconAttributes = iconId ? ` Icon="${xmlAttr(iconId)}" IconIndex="0"` : '';
  return `
    <ComponentGroup Id="DesktopShortcutComponents" Directory="DesktopFolder">
      <Component Id="DesktopShortcutComponent">
        <Shortcut Id="desktopShortcut" Directory="DesktopFolder" Name="${xmlAttr(productName)}" Description="Open ${xmlAttr(productName)}" Target="[#mainExecutable]" WorkingDirectory="APPLICATIONFOLDER"${iconAttributes} Advertise="no"/>
        <RegistryValue Root="HKCU" Key="Software\\${xmlAttr(productName)}" Name="DesktopShortcut" Type="integer" Value="1" KeyPath="yes"/>
      </Component>
    </ComponentGroup>`;
}

function buildStartupComponentGroup(productName) {
  return `
    <ComponentGroup Id="StartupComponents" Directory="APPLICATIONFOLDER">
      <Component Id="BackgroundStartupComponent">
        <RegistryValue Root="HKLM" Key="Software\\Microsoft\\Windows\\CurrentVersion\\Run" Name="${xmlAttr(productName)}" Type="string" Value="&quot;[#mainExecutable]&quot; --background-startup" KeyPath="yes"/>
      </Component>
    </ComponentGroup>`;
}

function buildUiBlock() {
  return [
    `<WixVariable Id="WixUILicenseRtf" Value="${xmlAttr(windowsPath(licenseFilePath))}"/>`,
    '<UIRef Id="WixUI_Mondo"/>',
  ].join('\n      ');
}

function buildCustomActionBlock() {
  const installCommand = xmlAttr(buildInstallTasksCommand());
  const removeTasksCommand = xmlAttr(buildRemoveTasksCommand());
  const removeMachineDataCommand = xmlAttr(buildRemoveMachineDataCommand());
  const promptRemoveUserDataCommand = xmlAttr(buildRemoveUserDataCommand('prompt'));
  const forceRemoveUserDataCommand = xmlAttr(buildRemoveUserDataCommand('force'));

  return `
    ${MARKER_START}
    <Property Id="REMOVEUSERDATA" Secure="yes" Value="0"/>
    <Property Id="WixQuietExecCmdLine" Hidden="yes"/>
    <CustomActionRef Id="${WIX_QUIET_EXEC_REF_ID}"/>
    <SetProperty Id="PixelPilotRollbackTasks" Value="${removeTasksCommand}" Before="PixelPilotRollbackTasks" Sequence="execute"/>
    <CustomAction Id="PixelPilotRollbackTasks" BinaryKey="${WIX_QUIET_EXEC_BINARY_KEY}" DllEntry="${WIX_QUIET_EXEC_REF_ID}" Execute="rollback" Impersonate="no" Return="ignore"/>
    <SetProperty Id="PixelPilotInstallTasks" Value="${installCommand}" Before="PixelPilotInstallTasks" Sequence="execute"/>
    <CustomAction Id="PixelPilotInstallTasks" BinaryKey="${WIX_QUIET_EXEC_BINARY_KEY}" DllEntry="${WIX_QUIET_EXEC_REF_ID}" Execute="deferred" Impersonate="no" Return="check"/>
    <SetProperty Id="PixelPilotRemoveTasks" Value="${removeTasksCommand}" Before="PixelPilotRemoveTasks" Sequence="execute"/>
    <CustomAction Id="PixelPilotRemoveTasks" BinaryKey="${WIX_QUIET_EXEC_BINARY_KEY}" DllEntry="${WIX_QUIET_EXEC_REF_ID}" Execute="deferred" Impersonate="no" Return="ignore"/>
    <SetProperty Id="PixelPilotRemoveMachineData" Value="${removeMachineDataCommand}" Before="PixelPilotRemoveMachineData" Sequence="execute"/>
    <CustomAction Id="PixelPilotRemoveMachineData" BinaryKey="${WIX_QUIET_EXEC_BINARY_KEY}" DllEntry="${WIX_QUIET_EXEC_REF_ID}" Execute="deferred" Impersonate="no" Return="ignore"/>
    <SetProperty Id="PixelPilotPromptRemoveUserData" Value="${promptRemoveUserDataCommand}" Before="PixelPilotPromptRemoveUserData" Sequence="execute"/>
    <CustomAction Id="PixelPilotPromptRemoveUserData" BinaryKey="${WIX_QUIET_EXEC_BINARY_KEY}" DllEntry="${WIX_QUIET_EXEC_REF_ID}" Execute="immediate" Impersonate="yes" Return="check"/>
    <SetProperty Id="PixelPilotForceRemoveUserData" Value="${forceRemoveUserDataCommand}" Before="PixelPilotForceRemoveUserData" Sequence="execute"/>
    <CustomAction Id="PixelPilotForceRemoveUserData" BinaryKey="${WIX_QUIET_EXEC_BINARY_KEY}" DllEntry="${WIX_QUIET_EXEC_REF_ID}" Execute="immediate" Impersonate="yes" Return="check"/>
    <InstallExecuteSequence>
      <Custom Action="PixelPilotRollbackTasks" Before="PixelPilotInstallTasks">NOT Installed</Custom>
      <Custom Action="PixelPilotInstallTasks" After="InstallFiles">NOT Installed</Custom>
      <Custom Action="PixelPilotRemoveTasks" Before="RemoveFiles">REMOVE="ALL"</Custom>
      <Custom Action="PixelPilotRemoveMachineData" After="PixelPilotRemoveTasks">REMOVE="ALL"</Custom>
      <Custom Action="PixelPilotPromptRemoveUserData" After="PixelPilotRemoveMachineData">REMOVE="ALL" AND UILevel &gt;= 4 AND REMOVEUSERDATA &lt;&gt; "1"</Custom>
      <Custom Action="PixelPilotForceRemoveUserData" After="PixelPilotRemoveMachineData">REMOVE="ALL" AND REMOVEUSERDATA="1"</Custom>
    </InstallExecuteSequence>
    ${MARKER_END}`;
}

exports.default = async function customizeMsiProject(projectFilePath) {
  ensureFileExists(licenseFilePath, 'MSI license file');
  ensureFileExists(removeUserDataScriptPath, 'MSI user-data cleanup script');

  let source = fs.readFileSync(projectFilePath, 'utf-8');
  source = stripExistingCustomization(source);

  const productNameMatch = source.match(/<Product\b[^>]* Name="([^"]+)"/);
  const iconIdMatch = source.match(/<Icon Id="([^"]+)"/);
  const productName = productNameMatch?.[1] || 'PixelPilot';
  const iconId = iconIdMatch?.[1] || null;

  source = replaceOrThrow(
    source,
    /<UIRef Id="WixUI_InstallDir"\/>\s*<UI>[\s\S]*?<\/UI>/,
    buildUiBlock(),
    'default MSI UI block',
  );

  source = replaceOrThrow(
    source,
    /<!-- Desktop link -->\s*(?:<Directory Id="DesktopFolder" Name="Desktop"\/>\s*)?<!-- Start menu link -->/,
    ['<!-- Desktop link -->', '      <Directory Id="DesktopFolder" Name="Desktop"/>', '', '      <!-- Start menu link -->'].join('\n'),
    'desktop directory block',
  );

  source = replaceOrThrow(
    source,
    /<Feature Id="ProductFeature" Absent="disallow">\s*<ComponentGroupRef Id="ProductComponents"\/>\s*<\/Feature>/,
    buildFeatureBlock(productName),
    'default product feature block',
  );

  source = replaceOrThrow(
    source,
    /<Component>\s*(<File\b[^>]*\bId="mainExecutable"[^>]*>)/,
    `<Component Id="${MAIN_EXECUTABLE_COMPONENT_ID}">\n$1`,
    'main executable component',
  );

  source = replaceOrThrow(
    source,
    /(<ComponentGroup Id="ProductComponents" Directory="APPLICATIONFOLDER">[\s\S]*?<\/ComponentGroup>)/,
    `$1\n${buildShortcutComponentGroup(productName, iconId)}\n${buildStartupComponentGroup(productName)}`,
    'product components block',
  );

  source = replaceOrThrow(
    source,
    /\s*<\/Product>\s*<\/Wix>\s*$/,
    `\n${buildCustomActionBlock()}\n\n  </Product>\n</Wix>\n`,
    'closing product block',
  );

  fs.writeFileSync(projectFilePath, source, 'utf-8');
};
