package(default_visibility = ["//visibility:public"])
load("@pybind11_bazel//:build_defs.bzl", "pybind_library_test")

cc_library(
    name = "native_test_util",
    testonly = 1,
    srcs = ["native_test_util.cc"],
    hdrs = ["native_test_util.h"],
    deps = [
        "//src/googleclouddebugger:common",
        "//src/googleclouddebugger:python_util",
        "@com_google_googletest//:gtest",
        "@com_google_absl//absl/strings",
        "@com_google_absl//absl/flags:flag",
        "@com_google_re2//:re2",
        "@gulrak_filesystem//:filesystem",
    ],
)

cc_library(
    name = "test_main",
    testonly = 1,
    srcs = ["test_main.cc"],
    deps = [
        "//src/googleclouddebugger:common",
        "@com_github_google_glog//:glog",
        "@com_google_googletest//:gtest",
        "@com_google_benchmark//:benchmark",
	#"@python38//:libpython",
        "@local_config_python//:python_headers",
        "@local_config_python//:python_embed",
        "@com_github_gflags_gflags//:gflags",
    ],
)

cc_test(
    name = "bytecode_manipulator_test",
    srcs = ["bytecode_manipulator_test.cc"],
    deps = [
        "//src/googleclouddebugger:bytecode_manipulator",
        "@com_google_googletest//:gtest_main",
    ],
)

pybind_library_test(
    name = "immutability_tracer_test",
    srcs = ["immutability_tracer_test.cc"],
    linkstatic = 1,  # recommended for microbenchmarks
    deps = [
        ":native_test_util",
        ":test_main",
        "//src/googleclouddebugger:immutability_tracer",
        "@com_google_googletest//:gtest",
    ],
)

pybind_library_test(
    name = "bytecode_breakpoint_test",
    srcs = ["bytecode_breakpoint_test.cc"],
    deps = [
        ":native_test_util",
        ":test_main",
        "//src/googleclouddebugger:bytecode_breakpoint",
        "//src/googleclouddebugger:common",
        "//src/googleclouddebugger:python_callback",
        "//src/googleclouddebugger:python_util",
        #"@local_config_python//:python_headers",
        "@com_google_googletest//:gtest",
        "@com_google_absl//absl/strings",
    ],
)

pybind_library_test(
    name = "python_callback_test",
    srcs = ["python_callback_test.cc"],
    deps = [
        ":native_test_util",
        ":test_main",
        "//src/googleclouddebugger:python_callback",
        "//src/googleclouddebugger:python_util",
        "@com_google_googletest//:gtest",
    ],
)

