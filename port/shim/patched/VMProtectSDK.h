#pragma once

// No-op VMProtect shim for the browser build.  The original SDK is a
// Windows-only obfuscation library; the browser port does not use it.
#define VMProtectBegin(name)
#define VMProtectBeginVirtualization(name)
#define VMProtectBeginMutation(name)
#define VMProtectBeginUltra(name)
#define VMProtectEnd()
#define VMProtectIsDebuggerPresent(x) 0
#define VMProtectIsVirtualMachinePresent() 0
#define VMProtectIsValidImageCRC() 1
