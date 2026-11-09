// Test/build-only preload. npm ci runs before this guard is enabled.
const net = require('node:net');
const dns = require('node:dns');
const dgram = require('node:dgram');
const local = (host) => host === 'localhost' || host === '::1' ||
  (net.isIP(String(host)) === 4 && String(host).startsWith('127.'));
const denied = () => { throw new Error('Offline CI blocked an external network operation'); };
dgram.Socket.prototype.send = denied;
dgram.Socket.prototype.connect = denied;
dns.lookupService = denied;
dns.promises.lookupService = denied;
for (const Resolver of [dns.Resolver, dns.promises.Resolver]) {
  for (const name of Object.getOwnPropertyNames(Resolver.prototype)) {
    if (name.startsWith('resolve') || name === 'reverse') Resolver.prototype[name] = denied;
  }
}
const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
  const normalized = Array.isArray(args[0]) ? args[0] : args;
  const options = normalized[0];
  const host = typeof options === 'object' ? options.host : typeof normalized[1] === 'string' ? normalized[1] : 'localhost';
  // Numeric string ports are TCP too. A remote UNC pipe is not local IPC.
  const pipe = typeof options === 'object' ? options.path :
    typeof options === 'string' && Number.isNaN(Number(options)) ? options : undefined;
  if (pipe && pipe.startsWith('\\\\') && !pipe.startsWith('\\\\.\\pipe\\')) denied();
  if (!pipe && !local(host || 'localhost')) denied();
  return connect.apply(this, args);
};
const lookup = dns.lookup;
dns.lookup = function (host, ...args) {
  if (!local(host)) denied();
  return lookup.call(this, host, ...args);
};
for (const name of Object.keys(dns)) {
  if (name.startsWith('resolve') || name === 'reverse') dns[name] = denied;
}
const promiseLookup = dns.promises.lookup;
dns.promises.lookup = function (host, ...args) {
  if (!local(host)) denied();
  return promiseLookup.call(this, host, ...args);
};
for (const name of Object.keys(dns.promises)) {
  if (name.startsWith('resolve') || name === 'reverse') dns.promises[name] = denied;
}
