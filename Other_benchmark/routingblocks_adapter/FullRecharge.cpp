#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <routingblocks/FullRechargeEvaluation.h>
#include <routingblocks_bindings/binding_helpers.hpp>

namespace routingblocks::bindings {
void bind_fullcharge(pybind11::module_& m) {
    pybind11::class_<FullRechargeEvaluation,Evaluation>(m,"FullRechargeEvaluation")
      .def(pybind11::init<double,double,double,double>())
      .def_readwrite("overload_penalty_factor",&FullRechargeEvaluation::overload_penalty_factor)
      .def_readwrite("resource_penalty_factor",&FullRechargeEvaluation::resource_penalty_factor)
      .def_readwrite("time_shift_penalty_factor",&FullRechargeEvaluation::time_shift_penalty_factor)
      .def_readwrite("deadline_s",&FullRechargeEvaluation::deadline_s)
      .def("replay",&FullRechargeEvaluation::replay);
    pybind11::class_<FullRechargeVertexData>(m,"FullRechargeVertexData")
      .def(pybind11::init<double,double,double,double,double>());
    pybind11::class_<FullRechargeArcData>(m,"FullRechargeArcData")
      .def(pybind11::init<double,double,double>());
    m.def("create_fullcharge_vertex",&::bindings::helpers::vertex_constructor<FullRechargeVertexData>);
    m.def("create_fullcharge_arc",&::bindings::helpers::arc_constructor<FullRechargeArcData>);
    pybind11::class_<FRVCP<FullRechargeDPLabel>>(m,"FullRechargeFacilityPlacementOptimizer")
      .def(pybind11::init([](const Instance& instance,double battery,double alpha,double deadline) {
        return FRVCP<FullRechargeDPLabel>(instance,std::make_shared<Propagator<FullRechargeDPLabel>>(instance,battery,alpha,deadline));
      }),pybind11::keep_alive<1,2>())
      .def("optimize",&FRVCP<FullRechargeDPLabel>::optimize);
}
}
