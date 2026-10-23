// Container has no IPv6: rewrite gRPC bind address "[::]:port" to "0.0.0.0:port".
const grpc = require(require.resolve('@grpc/grpc-js', { paths: [process.cwd()] }));
const orig = grpc.Server.prototype.bindAsync;
grpc.Server.prototype.bindAsync = function (addr, creds, cb) {
  return orig.call(this, String(addr).replace(/^\[::\]:/, '0.0.0.0:'), creds, cb);
};
