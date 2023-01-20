// This runs all the tests with Global Interpreter Lock held.
//
// main() function for gunit-based tests that need the python interpreter.
// The initialization is a bit complex, especially when it comes to using
// threads, so this file takes care of that.

#include <benchmark/benchmark.h>
#include <gflags/gflags.h>
#include <gtest/gtest.h>

#include "src/googleclouddebugger/common.h"

ABSL_FLAG(bool, python_multi_threaded, true,
          "If true, initializes the python interpreter in multi-thread mode "
          "for all tests and benchmarks. If false, the interpreter is "
          "initialized without thread support, i.e., no Global Interpreter "
          "Lock is created, i.e., PyEval_InitThreads() is not called");

int main(int argc, char** argv) {
  absl::SetFlag(&FLAGS_logtostderr, true);
  testing::InitGoogleTest(&argc, argv);
  gflags::ParseCommandLineFlags(&argc, &argv, true);

  Py_Initialize();


  int ret = 0;

  if (absl::GetFlag(FLAGS_python_multi_threaded)) {
    // Enable thread support (creates the GIL (global interpreter lock)).
    // The GIL is acquired by this function.
    PyEval_InitThreads();

    // Run all tests with Global Interpreter Lock held. The test code may
    // use "Py_BEGIN_ALLOW_THREADS" and "Py_END_ALLOW_THREADS" to allow
    // other Python threads to run.
    {
      benchmark::RunSpecifiedBenchmarks();  // then exit?
      ret = RUN_ALL_TESTS();
    }
  } else {
    benchmark::RunSpecifiedBenchmarks(); // then exit?
    ret = RUN_ALL_TESTS();
  }

  Py_Finalize();

  return ret;
}
