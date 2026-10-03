const fs = require('fs');
const path = require('path');

const envPath = path.resolve(__dirname, '../.env');
const targetPath = path.resolve(__dirname, './src/environments/environment.ts');

let envConfig = {};

if (fs.existsSync(envPath)) {
  const envFile = fs.readFileSync(envPath, 'utf8');
  const lines = envFile.split(/\r?\n/);
  lines.forEach(line => {
    const match = line.match(/^\s*([\w.-]+)\s*=\s*(.*)?\s*$/);
    if (match) {
      const key = match[1];
      let value = match[2] || '';
      if (value.length > 0 && value.charAt(0) === '"' && value.charAt(value.length - 1) === '"') {
        value = value.substring(1, value.length - 1);
      }
      envConfig[key] = value;
    }
  });
}

// 嚴格校驗
const required = [
  'FRONTEND_API_URL',
  'INVENTORY_ITEM_SERVICE_HOST',
  'INVENTORY_ITEM_SERVICE_PORT',
  'ACCOUNTING_SERVICE_HOST',
  'ACCOUNTING_SERVICE_PORT',
  'OTEL_COLLECTOR_ENDPOINT_HTTP'
];

required.forEach(v => {
  if (!envConfig[v]) {
    console.error(`\x1b[31m[Env Error] 缺少必要環境變數: ${v}\x1b[0m`);
    process.exit(1);
  }
});

// 選用: accounting API 的 bearer token（未設定時為空字串，不送 Authorization header）
const accountingToken = JSON.stringify(envConfig.ACCOUNTING_SPA_TOKEN || '');

const envConfigFile = `export const environment = {
  production: false,
  apiUrl: '${envConfig.FRONTEND_API_URL}',
  inventoryServiceHost: '${envConfig.INVENTORY_ITEM_SERVICE_HOST}',
  inventoryServicePort: '${envConfig.INVENTORY_ITEM_SERVICE_PORT}',
  accountingServiceHost: '${envConfig.ACCOUNTING_SERVICE_HOST}',
  accountingServicePort: '${envConfig.ACCOUNTING_SERVICE_PORT}',
  otelEndpoint: '${envConfig.OTEL_COLLECTOR_ENDPOINT_HTTP}',
  accountingToken: ${accountingToken}
};
`;

console.log('Generating environment.ts...');
if (!fs.existsSync(path.dirname(targetPath))) {
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
}
fs.writeFileSync(targetPath, envConfigFile);

// 更新 angular.json
const angularJsonPath = path.resolve(__dirname, './angular.json');
if (fs.existsSync(angularJsonPath) && envConfig.ALLOWED_HOSTS) {
  console.log('Updating angular.json allowedHosts...');
  const angularJson = JSON.parse(fs.readFileSync(angularJsonPath, 'utf8'));
  const hosts = envConfig.ALLOWED_HOSTS.split(',').map(h => h.trim()).filter(h => h);
  
  if (angularJson.projects && angularJson.projects['inventory-ui'] && 
      angularJson.projects['inventory-ui'].architect && 
      angularJson.projects['inventory-ui'].architect.serve && 
      angularJson.projects['inventory-ui'].architect.serve.options) {
    
    angularJson.projects['inventory-ui'].architect.serve.options.allowedHosts = hosts;
    fs.writeFileSync(angularJsonPath, JSON.stringify(angularJson, null, 2) + '\n');
    console.log('angular.json updated successfully.');
  }
}
