package(default_visibility = ["//visibility:public"])

cc_library(
    name = "common",
    hdrs = ["common.h"],
    deps = [
        "@com_github_google_glog//:glog",
        "@local_config_python//:python_headers",
    ],
)

cc_library(
    name = "nullable",
    hdrs = ["nullable.h"],
    deps = [
        ":common",
    ],
)

cc_library(
    name = "python_util",
    srcs = ["python_util.cc"],
    hdrs = ["python_util.h"],
    deps = [
        ":common",
        ":nullable",
	"//src/third_party:pylinetable",
    ],
)


cc_library(
    name = "python_callback",
    srcs = ["python_callback.cc"],
    hdrs = ["python_callback.h"],
    deps = [
        ":common",
        ":python_util",
    ],
)

cc_library(
    name = "leaky_bucket",
    srcs = ["leaky_bucket.cc"],
    hdrs = ["leaky_bucket.h"],
    deps = [
        ":common",
    ],
)

cc_library(
    name = "rate_limit",
    srcs = ["rate_limit.cc"],
    hdrs = ["rate_limit.h"],
    deps = [
        ":common",
        ":leaky_bucket",
    ],
)

cc_library(
    name = "bytecode_manipulator",
    srcs = ["bytecode_manipulator.cc"],
    hdrs = ["bytecode_manipulator.h"],
    deps = [
        ":common",
    ],
)

cc_library(
    name = "bytecode_breakpoint",
    srcs = ["bytecode_breakpoint.cc"],
    hdrs = ["bytecode_breakpoint.h"],
    deps = [
        ":bytecode_manipulator",
        ":common",
        ":python_callback",
        ":python_util",
    ],
)

cc_library(
    name = "immutability_tracer",
    srcs = ["immutability_tracer.cc"],
    hdrs = ["immutability_tracer.h"],
    deps = [
        ":common",
        ":python_util",
    ],
)

cc_library(
    name = "conditional_breakpoint",
    srcs = ["conditional_breakpoint.cc"],
    hdrs = ["conditional_breakpoint.h"],
    deps = [
        ":common",
        ":immutability_tracer",
        ":python_util",
        ":rate_limit",
        ":leaky_bucket",
    ],
)
