from util import database, toolchain, bitdiff, progress


with database.transact() as db:
    for device_name, device in db.items():
        progress(device_name)

        package, pinout = next(iter(device['pins'].items()))
        goe_muxes = {name: switch['mux']
                     for name, switch in device['globals'].items()
                     if name.startswith('GOE')}

        all_goe_choices = set()
        unique_goe_choices = set()
        for goe_name, goe_mux in goe_muxes.items():
            for goe_choice in goe_mux['values']:
                if goe_choice in unique_goe_choices:
                    unique_goe_choices.remove(goe_choice)
                elif goe_choice not in all_goe_choices:
                    unique_goe_choices.add(goe_choice)
                all_goe_choices.add(goe_choice)

        for macrocell_name, macrocell in device['macrocells'].items():
            if macrocell['pad'] not in pinout:
                progress()
                print(f"Skipping {macrocell_name} on {device_name} because it is not bonded out")
                continue
            else:
                progress(1)

            goe_pads = []
            for goe_name, goe_mux in goe_muxes.items():
                for goe_choice in goe_mux['values']:
                    if not goe_choice.endswith('_PAD'): continue
                    if goe_choice[:-4] == macrocell['pad']: continue
                    if goe_choice not in unique_goe_choices: continue
                    goe_pads.append(goe_choice)
                    break
                else:
                    progress()
                    print(f"GOE mux {goe_name} is only connected to pad {macrocell['pad']}!")

            if len(goe_pads) < len(goe_muxes):
                print(f"Skipping OE mux fuzzing for {macrocell_name} on {device_name}!")
                continue

            def run(code, **kwargs):
                return toolchain.run(
                    f"module top(input CLK1, {', '.join(goe_pads)}, output O); "
                    f"wire Y; OR{len(goe_pads)+1} o({', '.join(goe_pads)}, CLK1, Y); "
                    f"{code} "
                    f"endmodule",
                    {
                        'O': pinout[macrocell['pad']],
                        'CLK1': pinout['C1'],
                        **{
                            goe_pad: pinout[goe_pad[:-4]]
                            for goe_pad in goe_pads
                        },
                    },
                    f"{device_name}-{package}",
                    **kwargs)

            nodes = {}

            for (goe_name, goe_mux), goe_pad in zip(goe_muxes.items(), goe_pads):
                f_goe = run(f"TRI t(Y, {goe_pad}, O);", strategy={"Global_OE": goe_pad})
                for offset, goe_mux_fuse in enumerate(goe_mux['fuses']):
                    # We know what the GOE mux choice is.
                    assert f_goe[goe_mux_fuse] == (goe_mux['values'][goe_pad] >> offset) & 1
                    f_goe[goe_mux_fuse] = 1 # don't care
                nodes[goe_name] = f_goe

            f_gnd = run(f"wire BY; BIBUF b(Y, 1'b0, BY, O);")
            nodes['GND'] = f_gnd

            # The VCC choice of OE mux is shared with PT5 choice; if pt5_func is as, or
            # pt5_func is oe but pt5_mux is sum, then it is VCC, otherwise it is PT5.
            # Choosing pure VCC is a bit annoying (it switches pt5_func and/or pt5_mux depending
            # on how you do it), so choose PT5 and mask out the PT5 fuses instead.
            f_vcc = run(f"wire BY; BIBUF b(Y, CLK1, BY, O);")
            for pt5_fuse in range(*device['macrocells'][macrocell_name]['pterm_ranges']['PT5']):
                f_vcc[pt5_fuse] = 0 # don't care
            nodes['VCC_pt5'] = f_vcc

            macrocell.update({
                'oe_mux':
                    bitdiff.describe(3, nodes),
            })
